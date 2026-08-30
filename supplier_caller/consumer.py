"""Queue consumer: turns queued call requests into LiveKit calls, one semaphore per workflow.

Message lifecycle (SQS semantics):
  receive (lease = workflow.visibility_timeout_s) → dispatcher.run_call → delete on success.
  On exception: make the message visible again after `retry_delay` (so it is re-received; receive_count++).
  The queue's max_receive_count (== workflow.max_attempts) dead-letters it after the last failure.
  Unknown workflow → fail() straight to the DLQ.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from .catalog import Catalog
from .jobs import JobStore
from .queue import LocalQueue, Message

logger = logging.getLogger("dme-consumer")


class Dispatcher(Protocol):
    async def run_call(self, job: dict[str, Any]) -> dict[str, Any]: ...


class Consumer:
    def __init__(
        self,
        *,
        queue: LocalQueue,
        store: JobStore,
        catalog: Catalog,
        dispatcher: Dispatcher,
        poll_interval: float = 1.0,
        retry_delay: float = 5.0,
    ) -> None:
        self.queue, self.store, self.catalog, self.dispatcher = queue, store, catalog, dispatcher
        self.poll_interval, self.retry_delay = poll_interval, retry_delay
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    def _sem(self, workflow_id: str) -> asyncio.Semaphore:
        if workflow_id not in self._sems:
            self._sems[workflow_id] = asyncio.Semaphore(self.catalog.workflow(workflow_id).concurrency)
        return self._sems[workflow_id]

    def _capacity(self, workflow_id: str) -> bool:
        return self.catalog.workflows.get(workflow_id) is None or not self._sem(workflow_id).locked()

    async def run(self) -> None:
        while True:
            # Only pull messages we can actually start: leave the rest leased to no one.
            for msg in self.queue.peek():
                wid = msg.body.get("workflow_id", "")
                if msg.visible_at > time.time():
                    continue
                if not self._capacity(wid):
                    continue
                wf = self.catalog.workflows.get(wid)
                lease = wf.visibility_timeout_s if wf else 30
                got = self.queue.receive(max_messages=1, visibility_timeout=lease)
                for m in got:
                    self._idle.clear()
                    t = asyncio.create_task(self._handle(m))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                break  # re-peek after each receive so ordering/capacity stay honest
            if not self._tasks and not self.queue.peek():
                self._idle.set()
            await asyncio.sleep(self.poll_interval)

    async def drain(self, timeout: float) -> None:
        """Test helper: wait until the queue is empty and no calls are running."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not self._tasks and not self.queue.peek():
                return
            await asyncio.sleep(0.01)
        raise TimeoutError("consumer did not drain")

    async def _handle(self, msg: Message) -> None:
        job = msg.body
        job_id, wid = job["job_id"], job.get("workflow_id", "")
        wf = self.catalog.workflows.get(wid)
        if wf is None:
            self.store.update(job_id, status="failed", error=f"unknown workflow {wid!r}")
            self.queue.fail(msg.id, reason=f"unknown workflow {wid!r}")
            return
        async with self._sem(wid):
            self.store.update(job_id, status="running", attempts=msg.receive_count)
            try:
                result = await asyncio.wait_for(self.dispatcher.run_call(job), timeout=wf.call_timeout_s)
            except Exception as e:  # noqa: BLE001 — any failure follows the retry/DLQ policy
                last = msg.receive_count >= self.queue.max_receive_count
                logger.warning("job %s attempt %s failed: %s (%s)", job_id, msg.receive_count, e, "dead-letter" if last else "retry")
                if last:
                    self.store.update(job_id, status="failed", error=str(e))
                    self.queue.fail(msg.id, reason=str(e))
                else:
                    self.store.update(job_id, status="queued", error=f"attempt {msg.receive_count}: {e}")
                    self.queue.change_visibility(msg.id, visibility_timeout=self.retry_delay)
                return
            self.store.update(job_id, status="done", result=result, room=result.get("room"))
            self.queue.delete(msg.id)
