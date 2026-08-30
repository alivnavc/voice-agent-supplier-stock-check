"""CLI: place one call directly (no queue).

  python -m supplier_caller.run_call                                  # first patient -> first supplier's number
  python -m supplier_caller.run_call --patient pt-robert-chen
  python -m supplier_caller.run_call --phone +13125550100              # dial a different number
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from .catalog import Catalog
from .dispatch import LiveKitDispatcher


def summarize(result: dict) -> str:
    head = (
        f"OUTCOME: {result['outcome'].upper()}  (succeeded={result['succeeded']})  "
        f"duration={result['duration_seconds']}s  holds={result['holds']}"
    )
    lines = [head]
    for q, a in result["answers"].items():
        lines.append(f"  ✓ {q:17} = {a['value']!r:24} [{a['confidence']}]  “{a['quote']}”")
    for q in result["unanswered"]:
        lines.append(f"  ✗ {q:17} = (not obtained)")
    if result.get("callee_questions"):
        lines.append(f"  callee asked: {result['callee_questions']}")
    if result.get("code_readbacks"):
        lines.append(f"  code readbacks: {result['code_readbacks']}")
    if result.get("notes"):
        lines.append(f"  notes: {result['notes']}")
    lines.append(f"  recording: {result.get('recording')}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> dict:
    catalog = Catalog.load()
    req = catalog.build_request(args.patient, args.supplier, phone=args.phone)
    job = {
        "job_id": f"cli-{uuid.uuid4().hex[:8]}",
        "workflow_id": args.workflow,
        "request": req.to_dict(),
    }
    d = LiveKitDispatcher(catalog)
    room = await d.start_call(job)
    print(f"[run_call] room={room} dialing={req.supplier.phone} patient={req.patient.name} — waiting…")
    return await d.wait_for_result(room, timeout=args.timeout)


def main() -> None:
    catalog = Catalog.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default=catalog.patients[0].id, choices=[p.id for p in catalog.patients])
    ap.add_argument("--supplier", default=catalog.suppliers[0].id, choices=[s.id for s in catalog.suppliers])
    ap.add_argument("--workflow", default="supplier_check", choices=sorted(catalog.workflows))
    ap.add_argument("--phone", help="E.164 number to dial instead of the supplier's number")
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()
    print(summarize(asyncio.run(run(args))))


if __name__ == "__main__":
    main()
