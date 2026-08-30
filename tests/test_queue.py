"""SQS-shaped local queue with visibility timeout and dead-letter queue."""
from __future__ import annotations

import pytest

from supplier_caller.queue import LocalQueue


@pytest.fixture()
def q(tmp_path):
    dlq = LocalQueue(name="dlq", path=tmp_path / "dlq.json")
    return LocalQueue(name="main", path=tmp_path / "main.json", dlq=dlq, max_receive_count=2)


def test_send_receive_delete(q: LocalQueue) -> None:
    mid = q.send({"job": 1})
    msgs = q.receive(max_messages=10, visibility_timeout=30, now=100.0)
    assert [m.id for m in msgs] == [mid]
    assert msgs[0].body == {"job": 1}
    assert msgs[0].receive_count == 1
    # invisible while in flight
    assert q.receive(max_messages=10, visibility_timeout=30, now=110.0) == []
    q.delete(mid)
    assert q.receive(max_messages=10, visibility_timeout=30, now=200.0) == []
    assert q.stats()["queued"] == 0


def test_visibility_timeout_makes_message_reappear(q: LocalQueue) -> None:
    mid = q.send({"job": 2})
    q.receive(max_messages=1, visibility_timeout=30, now=100.0)
    again = q.receive(max_messages=1, visibility_timeout=30, now=131.0)
    assert [m.id for m in again] == [mid]
    assert again[0].receive_count == 2


def test_exceeding_max_receive_count_moves_to_dlq(q: LocalQueue) -> None:
    q.send({"job": 3})
    q.receive(max_messages=1, visibility_timeout=1, now=0.0)
    q.receive(max_messages=1, visibility_timeout=1, now=10.0)
    assert q.receive(max_messages=1, visibility_timeout=1, now=20.0) == []  # 3rd receive -> DLQ instead
    assert q.stats()["queued"] == 0
    assert q.dlq is not None and q.dlq.stats()["queued"] == 1
    dead = q.dlq.receive(max_messages=1, visibility_timeout=1, now=21.0)[0]
    assert dead.body == {"job": 3}
    assert dead.attributes["dead_letter_reason"] == "max_receive_count"


def test_fail_moves_to_dlq_immediately_with_reason(q: LocalQueue) -> None:
    mid = q.send({"job": 4})
    q.receive(max_messages=1, visibility_timeout=30, now=0.0)
    q.fail(mid, reason="unrecoverable: bad phone")
    assert q.stats()["queued"] == 0
    dead = q.dlq.receive(max_messages=1, visibility_timeout=1, now=1.0)[0]
    assert dead.attributes["dead_letter_reason"] == "unrecoverable: bad phone"


def test_redrive_from_dlq(q: LocalQueue) -> None:
    mid = q.send({"job": 5})
    q.receive(max_messages=1, visibility_timeout=30, now=0.0)
    q.fail(mid, reason="x")
    q.redrive(mid)
    assert q.stats()["queued"] == 1 and q.dlq.stats()["queued"] == 0
    msg = q.receive(max_messages=1, visibility_timeout=30, now=1.0)[0]
    assert msg.body == {"job": 5} and msg.receive_count == 1  # counter reset


def test_change_visibility_extends_lease(q: LocalQueue) -> None:
    mid = q.send({"job": 6})
    q.receive(max_messages=1, visibility_timeout=10, now=0.0)
    q.change_visibility(mid, visibility_timeout=100, now=5.0)
    assert q.receive(max_messages=1, visibility_timeout=10, now=50.0) == []


def test_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "q.json"
    q1 = LocalQueue(name="main", path=path)
    q1.send({"job": 7})
    q2 = LocalQueue(name="main", path=path)
    assert q2.stats()["queued"] == 1
    assert q2.receive(max_messages=1, visibility_timeout=1, now=0.0)[0].body == {"job": 7}
