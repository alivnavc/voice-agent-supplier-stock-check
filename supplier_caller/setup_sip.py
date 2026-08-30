"""Create the LiveKit outbound SIP trunk that points at your Plivo Zentrunk.

Needs in .env:  PLIVO_TRUNK_DOMAIN (or PLIVO_SIP_DOMAIN)=<trunk_id>.zt.plivo.com  PLIVO_SIP_USERNAME  PLIVO_SIP_PASSWORD
                PLIVO_NUMBER=+1XXXXXXXXXX   (must be a number rented from Plivo — caller ID rule)
Writes SIP_OUTBOUND_TRUNK_ID back into .env.
"""
from __future__ import annotations

import asyncio
import os

from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo, SIPTransport

from . import config


async def main() -> None:
    domain, user, pw, number = (
        os.getenv("PLIVO_TRUNK_DOMAIN") or os.getenv("PLIVO_SIP_DOMAIN"), os.getenv("PLIVO_SIP_USERNAME"),
        os.getenv("PLIVO_SIP_PASSWORD"), os.getenv("PLIVO_NUMBER"),
    )
    missing = [k for k, v in {"PLIVO_TRUNK_DOMAIN": domain, "PLIVO_SIP_USERNAME": user,
                              "PLIVO_SIP_PASSWORD": pw, "PLIVO_NUMBER": number}.items() if not v]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")
    lk = api.LiveKitAPI()
    try:
        info = await lk.sip.create_sip_outbound_trunk(
            CreateSIPOutboundTrunkRequest(
                trunk=SIPOutboundTrunkInfo(
                    name="Plivo Zentrunk outbound",
                    address=domain,
                    transport=SIPTransport.SIP_TRANSPORT_TCP,
                    numbers=[number],
                    auth_username=user,
                    auth_password=pw,
                )
            )
        )
    finally:
        await lk.aclose()
    print("created trunk", info.sip_trunk_id)
    env = config.ROOT / ".env"
    lines = [l for l in env.read_text().splitlines() if not l.startswith("SIP_OUTBOUND_TRUNK_ID=")]
    lines.append(f"SIP_OUTBOUND_TRUNK_ID={info.sip_trunk_id}")
    env.write_text("\n".join(lines) + "\n")
    print("wrote SIP_OUTBOUND_TRUNK_ID to .env")


if __name__ == "__main__":
    asyncio.run(main())
