"""HCPCS code handling for the phone line (alphanumerics are routinely misheard)."""
from __future__ import annotations

import re

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_LETTER_WORDS = {"kay": "K", "k": "K", "e": "E", "a": "A", "b": "B", "l": "L"}
_MULTIPLIERS = {"double": 2, "triple": 3}
_SPELLED_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d", text.lower())


def normalize_hcpcs(spoken: str) -> str | None:
    """Return a canonical HCPCS code (letter + 4 digits) found in speech or text, else None.

    Handles "K0001", "K 0001", "K-0-0-0-1", "K zero zero zero one", "kay oh oh oh one",
    "k triple zero one".
    """
    if not spoken:
        return None
    compact = re.search(r"\b([A-Za-z])[\s-]*(\d)[\s-]*(\d)[\s-]*(\d)[\s-]*(\d)\b", spoken)
    if compact:
        return (compact.group(1) + "".join(compact.groups()[1:])).upper()
    toks = _tokens(spoken)
    for i, tok in enumerate(toks):
        letter = _LETTER_WORDS.get(tok) or (tok.upper() if len(tok) == 1 and tok.isalpha() else None)
        if letter is None:
            continue
        digits: list[str] = []
        j = i + 1
        while j < len(toks) and len(digits) < 4:
            t = toks[j]
            if t in _MULTIPLIERS and j + 1 < len(toks):
                nxt = toks[j + 1]
                d = _DIGIT_WORDS.get(nxt) or (nxt if nxt.isdigit() else None)
                if d is None:
                    break
                digits.extend([d] * _MULTIPLIERS[t])
                j += 2
                continue
            d = _DIGIT_WORDS.get(t) or (t if t.isdigit() else None)
            if d is None:
                break
            digits.append(d)
            j += 1
        if len(digits) >= 4:
            return letter + "".join(digits[:4])
    return None


def spell_code(code: str) -> str:
    """'K0001' -> 'K zero zero zero one' (what the TTS should say)."""
    return " ".join([code[0].upper()] + [_SPELLED_DIGITS[int(c)] if c.isdigit() else c for c in code[1:]])


def readback_matches(expected: str, heard: str) -> bool | None:
    """True/False if a code was heard; None if no code could be found in `heard`."""
    got = normalize_hcpcs(heard)
    if got is None:
        return None
    return got == expected.upper()
