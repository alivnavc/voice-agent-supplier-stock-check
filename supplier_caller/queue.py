"""An SQS-shaped message queue.

`MessageQueue` is the interface the consumer and API depend on; `LocalQueue` is a file-persisted
implementation with SQS semantics (visibility timeout, receive count, redrive policy → dead-letter queue).
Swap in `SQSQueue` (same interface) when AWS credentials exist — nothing else changes.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Message:
    id: str
    body: dict[str, Any]
    sent_at: float
    receive_count: int = 0
    visible_at: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)


class MessageQueue(Protocol):
    name: str

    def send(self, body: dict[str, Any], **attributes: Any) -> str: ...
    def receive(self, *, max_messages: int = 1, visibility_timeout: float = 30.0, now: float | None = None) -> list[Message]: ...
    def delete(self, message_id: str) -> None: ...
    def change_visibility(self, message_id: str, *, visibility_timeout: float, now: float | None = None) -> None: ...
    def fail(self, message_id: str, *, reason: str) -> None: ...
    def stats(self) -> dict[str, int]: ...


class LocalQueue:
    """In-process queue persisted to a JSON file (so the API process and a restart see the same state)."""

    def __init__(
        self,
        *,
        name: str,
        path: Path | None = None,
        dlq: LocalQueue | None = None,
        max_receive_count: int = 3,
    ) -> None:
        self.name = name
        self.path = path
        self.dlq = dlq
        self.max_receive_count = max_receive_count
        self._lock = threading.RLock()
        self._messages: dict[str, Message] = {}
        if path and path.exists():
            for raw in json.loads(path.read_text()):
                self._messages[raw["id"]] = Message(**raw)

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(m) for m in self._messages.values()], indent=1))

    # -- SQS-like API ---------------------------------------------------------
    def send(self, body: dict[str, Any], **attributes: Any) -> str:
        with self._lock:
            mid = uuid.uuid4().hex
            self._messages[mid] = Message(id=mid, body=body, sent_at=time.time(), attributes=attributes)
            self._save()
            return mid

    def receive(self, *, max_messages: int = 1, visibility_timeout: float = 30.0, now: float | None = None) -> list[Message]:
        now = time.time() if now is None else now
        out: list[Message] = []
        with self._lock:
            for m in sorted(self._messages.values(), key=lambda m: m.sent_at):
                if len(out) >= max_messages:
                    break
                if m.visible_at > now:
                    continue
                if m.receive_count >= self.max_receive_count:
                    self._dead_letter(m, reason="max_receive_count")
                    continue
                m.receive_count += 1
                m.visible_at = now + visibility_timeout
                out.append(m)
            self._save()
        return out

    def delete(self, message_id: str) -> None:
        with self._lock:
            self._messages.pop(message_id, None)
            self._save()

    def change_visibility(self, message_id: str, *, visibility_timeout: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if m := self._messages.get(message_id):
                m.visible_at = now + visibility_timeout
                self._save()

    def fail(self, message_id: str, *, reason: str) -> None:
        """Give up on a message now (unrecoverable) — straight to the DLQ."""
        with self._lock:
            if m := self._messages.get(message_id):
                self._dead_letter(m, reason=reason)
                self._save()

    def redrive(self, message_id: str) -> None:
        """Move a message from the DLQ back onto this queue with a fresh receive count."""
        if self.dlq is None:
            raise ValueError("no dead-letter queue")
        with self._lock, self.dlq._lock:
            m = self.dlq._messages.pop(message_id, None)
            if m is None:
                raise KeyError(message_id)
            m.receive_count = 0
            m.visible_at = 0.0
            m.attributes = {k: v for k, v in m.attributes.items() if not k.startswith("dead_letter")}
            self._messages[m.id] = m
            self.dlq._save()
            self._save()

    def peek(self) -> list[Message]:
        with self._lock:
            return sorted(self._messages.values(), key=lambda m: m.sent_at)

    def stats(self) -> dict[str, int]:
        now = time.time()
        with self._lock:
            inflight = sum(1 for m in self._messages.values() if m.visible_at > now)
            return {"queued": len(self._messages), "in_flight": inflight, "dlq": self.dlq.stats()["queued"] if self.dlq else 0}

    def _dead_letter(self, m: Message, *, reason: str) -> None:
        self._messages.pop(m.id, None)
        if self.dlq is not None:
            m.visible_at = 0.0
            m.receive_count = 0
            m.attributes = {**m.attributes, "dead_letter_reason": reason, "dead_letter_at": time.time(), "source_queue": self.name}
            with self.dlq._lock:
                self.dlq._messages[m.id] = m
                self.dlq._save()


class SQSQueue:
    """Same interface over boto3 — the drop-in once AWS credentials exist.

    Mapping: send→send_message, receive→receive_message(VisibilityTimeout), delete→delete_message(ReceiptHandle),
    change_visibility→change_message_visibility, redrive policy configured on the queue (maxReceiveCount).
    Not implemented in this exercise; kept here so the seam is explicit.
    """

    def __init__(self, *, name: str, queue_url: str) -> None:
        raise NotImplementedError("SQSQueue: add boto3 + AWS credentials; interface is MessageQueue")
