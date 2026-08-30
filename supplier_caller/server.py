"""Control plane: a small FastAPI app that serves the UI, accepts call requests onto the queue,
runs the queue consumer in-process, and exposes jobs / results / recordings.

  python -m supplier_caller.server            # http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .catalog import Catalog
from .consumer import Consumer
from .dispatch import LiveKitDispatcher
from .jobs import JobStore
from .queue import LocalQueue

logger = logging.getLogger("dme-server")
STATE_DIR = config.ROOT / "data" / "state"
STATIC_DIR = Path(__file__).resolve().parent / "static"

catalog = Catalog.load()
dlq = LocalQueue(name="calls-dlq", path=STATE_DIR / "calls-dlq.json")
queue = LocalQueue(
    name="calls",
    path=STATE_DIR / "calls.json",
    dlq=dlq,
    max_receive_count=max(w.max_attempts for w in catalog.workflows.values()),
)
store = JobStore(path=STATE_DIR / "jobs.json")
consumer = Consumer(queue=queue, store=store, catalog=catalog, dispatcher=LiveKitDispatcher(catalog))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(consumer.run())
    yield
    task.cancel()


app = FastAPI(title="DME Supplier Caller", lifespan=lifespan)
config.RECORDINGS_DIR.mkdir(exist_ok=True)
app.mount("/recordings", StaticFiles(directory=config.RECORDINGS_DIR), name="recordings")


class CallRequestIn(BaseModel):
    patient_id: str
    supplier_id: str
    workflow_id: str = "supplier_check"
    phone: str | None = None  # dial this instead of the supplier's number


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text()


@app.get("/api/catalog")
async def get_catalog() -> dict[str, Any]:
    return {
        "patients": [p.__dict__ for p in catalog.patients],
        "suppliers": [s.__dict__ for s in catalog.suppliers],
        "workflows": {k: {**w.__dict__} for k, w in catalog.workflows.items()},
        "sip_ready": bool(config.os.getenv("SIP_OUTBOUND_TRUNK_ID")),
        "caller_id": config.os.getenv("PLIVO_NUMBER"),
    }


@app.post("/api/calls", status_code=202)
async def create_call(body: CallRequestIn) -> dict[str, Any]:
    try:
        wf = catalog.workflow(body.workflow_id)
        req = catalog.build_request(body.patient_id, body.supplier_id, phone=body.phone)
    except (KeyError, StopIteration) as e:
        raise HTTPException(400, f"unknown id: {e}") from e
    if not req.supplier.phone:
        raise HTTPException(400, "supplier has no phone number")
    job = store.create(req, workflow_id=wf.id)
    message_id = queue.send(job, workflow_id=wf.id)
    store.update(job["job_id"], message_id=message_id)
    return {"job": store.get(job["job_id"]), "message_id": message_id, "queue": queue.stats()}


@app.get("/api/jobs")
async def list_jobs() -> list[dict[str, Any]]:
    return [{k: v for k, v in j.items() if k != "result"} | {"outcome": (j.get("result") or {}).get("outcome")} for j in store.list()]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    try:
        job = store.get(job_id)
    except KeyError as e:
        raise HTTPException(404, "no such job") from e
    if job.get("room"):
        path = config.RESULTS_DIR / f"{job['room']}.json"
        if path.exists():
            job["result"] = json.loads(path.read_text())  # full result incl. transcript + audit
    return job


@app.get("/api/queue")
async def queue_state() -> dict[str, Any]:
    return {
        "stats": queue.stats(),
        "queued": [m.__dict__ for m in queue.peek()],
        "dlq": [m.__dict__ for m in dlq.peek()],
        "workflows": {k: {"concurrency": w.concurrency, "max_attempts": w.max_attempts} for k, w in catalog.workflows.items()},
    }


@app.post("/api/dlq/{message_id}/redrive")
async def redrive(message_id: str) -> dict[str, Any]:
    try:
        queue.redrive(message_id)
    except KeyError as e:
        raise HTTPException(404, "not in DLQ") from e
    msg = next(m for m in queue.peek() if m.id == message_id)
    store.update(msg.body["job_id"], status="queued", error=None)
    return {"queue": queue.stats()}


@app.get("/api/results/{room}")
async def get_result(room: str) -> FileResponse:
    path = config.RESULTS_DIR / f"{room}.json"
    if not path.exists():
        raise HTTPException(404, "no result yet")
    return FileResponse(path)


def main() -> None:
    uvicorn.run("supplier_caller.server:app", host="127.0.0.1", port=int(config.os.getenv("PORT", "8000")), reload=False)


if __name__ == "__main__":
    main()
