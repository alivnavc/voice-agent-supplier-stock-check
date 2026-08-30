"""Environment + model selection in one place (so the writeup can point at it)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The Deepgram plugin reads DEEPGRAM_API_KEY; the project .env names it DEEPGRAM_VOICE_API_KEY.
if "DEEPGRAM_API_KEY" not in os.environ and os.getenv("DEEPGRAM_VOICE_API_KEY"):
    os.environ["DEEPGRAM_API_KEY"] = os.environ["DEEPGRAM_VOICE_API_KEY"]

RECORDINGS_DIR = ROOT / "recordings"
RESULTS_DIR = ROOT / "results"

CALLER_AGENT_NAME = "supplier-caller"


@dataclass(frozen=True)
class Models:
    # gpt-4.1: strong parallel tool-calling, ~400ms TTFT, no hidden reasoning latency on a live call.
    caller_llm: str = os.getenv("CALLER_LLM", "gpt-4.1")
    # Offline second opinion over the transcript; latency doesn't matter here.
    audit_llm: str = os.getenv("AUDIT_LLM", "gpt-4.1")
    stt_model: str = os.getenv("STT_MODEL", "nova-3")
    # "en" (default): English + keyterm boosting for K0001/ZIPs.  "multi": Nova-3 auto-detects the language
    # per utterance and code-switches; keyterm boosting is English-only so it is dropped in that mode.
    stt_language: str = os.getenv("STT_LANGUAGE", "en")
    caller_voice: str = os.getenv("CALLER_VOICE", "aura-2-thalia-en")


MODELS = Models()

# Background-noise / interruption tuning. Raise to ignore more of the room around the phone; lower if the
# agent is too hard to interrupt when the supplier genuinely talks over it.
VAD_ACTIVATION = float(os.getenv("VAD_ACTIVATION", "0.6"))  # Silero speech probability; default 0.5
MIN_INTERRUPTION_SECONDS = float(os.getenv("MIN_INTERRUPTION_SECONDS", "0.6"))  # speech needed to cut the agent off
MIN_INTERRUPTION_WORDS = int(os.getenv("MIN_INTERRUPTION_WORDS", "2"))  # ...and at least this many words
FALSE_INTERRUPTION_TIMEOUT = float(os.getenv("FALSE_INTERRUPTION_TIMEOUT", "1.5"))  # no words in this long → resume

# Hold / duration guards (seconds)
HOLD_CHECKIN_AFTER = float(os.getenv("HOLD_CHECKIN_AFTER", "150"))
HOLD_GIVE_UP_AFTER = float(os.getenv("HOLD_GIVE_UP_AFTER", "300"))
MAX_CALL_SECONDS = float(os.getenv("MAX_CALL_SECONDS", "360"))
CALLEE_JOIN_TIMEOUT = float(os.getenv("CALLEE_JOIN_TIMEOUT", "90"))
