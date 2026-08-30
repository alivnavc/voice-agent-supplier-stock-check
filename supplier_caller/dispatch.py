"""Turn a job (request + workflow) into a LiveKit SIP call and wait for its result file.

Used by both the CLI (`run_call.py`) and the queue consumer (`consumer.py`).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from typing import Any

from livekit import api

from . import config
from .catalog import Catalog, WorkflowConfig


class LiveKitDispatcher:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    async def start_call(self, job: dict[str, Any]) -> str:
        """Create the room + caller-agent dispatch. The agent dials the supplier over SIP. Returns the room name."""
        wf: WorkflowConfig = self.catalog.workflow(job["workflow_id"])
        phone = job["request"]["supplier"].get("phone")
        trunk = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
        if not (phone and trunk):
            raise ValueError("need supplier.phone and SIP_OUTBOUND_TRUNK_ID (run: python -m supplier_caller.setup_sip)")
        room = f"{wf.id}-{job['job_id']}-{time.strftime('%H%M%S')}"
        meta: dict[str, Any] = {"job_id": job["job_id"], "request": job["request"], "phone": phone, "trunk_id": trunk}
        lk = api.LiveKitAPI()
        try:
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(agent_name=wf.caller_agent, room=room, metadata=json.dumps(meta))
            )
        finally:
            await lk.aclose()
        return room

    async def wait_for_result(self, room: str, timeout: float) -> dict[str, Any]:
        path = config.RESULTS_DIR / f"{room}.json"
        t0 = time.time()
        while not path.exists():
            if time.time() - t0 > timeout:
                raise TimeoutError(f"no result for {room} after {timeout:.0f}s")
            await asyncio.sleep(1.0)
        await asyncio.sleep(0.5)
        result = json.loads(path.read_text())
        if result.get("recording"):
            make_listenable(result["recording"])
            result["recording_mp3"] = result["recording"].rsplit(".", 1)[0] + ".mp3"
        result["room"] = room
        return result

    async def run_call(self, job: dict[str, Any]) -> dict[str, Any]:
        wf = self.catalog.workflow(job["workflow_id"])
        room = await self.start_call(job)
        return await self.wait_for_result(room, timeout=wf.call_timeout_s)


def make_listenable(ogg: str) -> None:
    """Stereo OGG (L=supplier, R=agent) -> mono MP3 anyone can play."""
    mp3 = ogg.rsplit(".", 1)[0] + ".mp3"
    if not os.path.exists(mp3):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ogg, "-ac", "1", "-b:a", "96k", mp3], check=False)
