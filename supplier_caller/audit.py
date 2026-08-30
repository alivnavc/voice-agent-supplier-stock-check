"""Post-call audit: a second model re-reads the transcript and checks the tool-recorded answers.

The live agent records answers as it hears them (fast, in the loop). This pass is slow and offline,
so it can be strict. Disagreements downgrade confidence; they never silently overwrite.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .config import MODELS
from .models import QUESTION_ORDER, CallState

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        q.value: {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "transcript_answer": {
                    "type": ["string", "null"],
                    "description": "What the SUPPLIER actually said about this, or null if never addressed.",
                },
                "agrees": {"type": ["boolean", "null"], "description": "Does the recorded answer match? null if not recorded or not addressed."},
                "note": {"type": "string"},
            },
            "required": ["transcript_answer", "agrees", "note"],
        }
        for q in QUESTION_ORDER
    },
    "required": [q.value for q in QUESTION_ORDER],
}


def transcript_text(transcript: list[dict[str, Any]]) -> str:
    lines = []
    for item in transcript:
        if item.get("type") != "message":
            continue
        role = "SUPPLIER" if item.get("role") == "user" else "AGENT"
        content = item.get("content")
        if isinstance(content, list):
            content = " ".join(c for c in content if isinstance(c, str))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def audit_call(state: CallState, transcript: list[dict[str, Any]]) -> dict[str, Any]:
    text = transcript_text(transcript)
    if not text.strip():
        return {}
    recorded = {q.value: f.value for q, f in state.findings.items()}
    p = state.request.patient
    client = AsyncOpenAI()
    resp = await client.chat.completions.create(
        model=MODELS.audit_llm,
        temperature=0,
        response_format={"type": "json_schema", "json_schema": {"name": "audit", "strict": True, "schema": _SCHEMA}},
        messages=[
            {
                "role": "system",
                "content": (
                    "You audit a phone transcript between our AGENT and a medical-equipment SUPPLIER. "
                    f"Patient ZIP {p.zip_code}; item HCPCS {p.hcpcs}. For each of the four questions, state what the "
                    "SUPPLIER actually said (or null), and whether the agent's recorded answer agrees. "
                    "Be literal: only the supplier's words count, not the agent's."
                ),
            },
            {"role": "user", "content": f"RECORDED ANSWERS:\n{json.dumps(recorded)}\n\nTRANSCRIPT:\n{text}"},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)
