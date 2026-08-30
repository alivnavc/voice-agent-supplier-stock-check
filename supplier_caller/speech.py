"""Make LLM text safe for a phone TTS: spell codes and ZIPs digit by digit."""
from __future__ import annotations

import re

from .codes import spell_code

_CODE_RE = re.compile(r"\b([A-Z])(\d{4})\b")
_ZIP_RE = re.compile(r"\b(\d{5})\b")
_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def speakable(text: str) -> str:
    text = _CODE_RE.sub(lambda m: spell_code(m.group(1) + m.group(2)), text)
    text = _ZIP_RE.sub(lambda m: " ".join(_DIGITS[int(c)] for c in m.group(1)), text)
    return text


_STAGE_DIRECTION = re.compile(r"^\s*[\(\[\*]\s*[^\)\]\*]{0,60}[\)\]\*][\s.!]*$")


def is_stage_direction(text: str) -> bool:
    """'(Silence)', '[no response]', '*stays quiet*' — text a model writes instead of saying nothing.
    Never speak it."""
    return bool(_STAGE_DIRECTION.match(text))
