"""Job records: one per requested call. Persisted to JSON so the UI survives restarts."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import CallRequest


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._jobs = json.loads(path.read_text())

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._jobs, indent=1, default=str))

    def create(self, request: CallRequest, *, workflow_id: str) -> dict[str, Any]:
        with self._lock:
            job = {
                "job_id": f"job-{uuid.uuid4().hex[:10]}",
                "workflow_id": workflow_id,
                "request": request.to_dict(),
                "status": "queued",
                "attempts": 0,
                "created_at": time.time(),
                "updated_at": time.time(),
                "room": None,
                "result": None,
                "error": None,
            }
            self._jobs[job["job_id"]] = job
            self._save()
            return dict(job)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields, updated_at=time.time())
            self._save()
            return dict(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._jobs[job_id])

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(j) for j in self._jobs.values()), key=lambda j: j["created_at"], reverse=True)
