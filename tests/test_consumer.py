"""Queue consumer: honours per-workflow concurrency, records job status, retries then dead-letters."""
from __future__ import annotations

import asyncio

import pytest

from supplier_caller.catalog import Catalog
from supplier_caller.consumer import Consumer
from supplier_caller.jobs import JobStore
from supplier_caller.queue import LocalQueue


class FakeDispatcher:
    def __init__(self, delay: float = 0.05, fail_ids: set[str] | None = None) -> None:
        self.delay, self.fail_ids = delay, fail_ids or set()
        self.active = 0
        self.max_active = 0
        self.calls: list[dict] = []

    async def run_call(self, job: dict) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            self.calls.append(job)
            if job["job_id"] in self.fail_ids:
                raise RuntimeError("livekit exploded")
            return {"outcome": "complete", "succeeded": True, "answers": {}, "unanswered": [], "recording": None}
        finally:
            self.active -= 1


@pytest.fixture()
def rig(tmp_path):
    catalog = Catalog.load()
    dlq = LocalQueue(name="dlq", path=tmp_path / "dlq.json")
    q = LocalQueue(name="calls", path=tmp_path / "calls.json", dlq=dlq, max_receive_count=2)
    store = JobStore(path=tmp_path / "jobs.json")
    return catalog, q, store


async def test_concurrency_is_capped_per_workflow(rig) -> None:
    catalog, q, store = rig
    disp = FakeDispatcher(delay=0.1)
    consumer = Consumer(queue=q, store=store, catalog=catalog, dispatcher=disp, poll_interval=0.01)
    for i in range(5):
        job = store.create(catalog.build_request("pt-eleanor-martinez", "sup-lakeview"), workflow_id="supplier_check")
        q.send(job)
    task = asyncio.create_task(consumer.run())
    await consumer.drain(timeout=5)
    task.cancel()
    assert len(disp.calls) == 5
    assert disp.max_active <= catalog.workflow("supplier_check").concurrency
    assert all(j["status"] == "done" for j in store.list())
    assert q.stats()["queued"] == 0


async def test_failure_retries_then_dead_letters(rig) -> None:
    catalog, q, store = rig
    job = store.create(catalog.build_request("pt-eleanor-martinez", "sup-lakeview"), workflow_id="supplier_check")
    disp = FakeDispatcher(delay=0.01, fail_ids={job["job_id"]})
    consumer = Consumer(queue=q, store=store, catalog=catalog, dispatcher=disp, poll_interval=0.01, retry_delay=0.01)
    q.send(job)
    task = asyncio.create_task(consumer.run())
    await consumer.drain(timeout=5)
    task.cancel()
    assert len(disp.calls) == 2  # max_attempts = 2
    assert store.get(job["job_id"])["status"] == "failed"
    assert q.dlq.stats()["queued"] == 1


async def test_unknown_workflow_goes_straight_to_dlq(rig) -> None:
    catalog, q, store = rig
    disp = FakeDispatcher()
    consumer = Consumer(queue=q, store=store, catalog=catalog, dispatcher=disp, poll_interval=0.01)
    job = store.create(catalog.build_request("pt-eleanor-martinez", "sup-lakeview"), workflow_id="nope")
    q.send(job)
    task = asyncio.create_task(consumer.run())
    await consumer.drain(timeout=3)
    task.cancel()
    assert disp.calls == []
    assert q.dlq.stats()["queued"] == 1
    assert store.get(job["job_id"])["status"] == "failed"
