"""How readable a passage of (often OCR'd) text is.

Scanned files come out anywhere between clean prose and ``ll),.,O 1 24-o.:l57``.
The score is the share of tokens that look like words, minus a penalty for
stray symbols, so pages can be ranked and the cleanest quoted first.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"\S+")
_WORD = re.compile(r"^[(\"'“‘]?(?:[A-Za-z]+(?:['’-][A-Za-z]+)*|\d{1,4}(?:[.,:/-]\d{1,4})*)[)\"'”’.,;:!?%]{0,3}$")
_STRAY = re.compile(r"[^A-Za-z0-9\s.,;:!?'\"()\-–/%$&]")


def readability(text: str | None) -> float:
    """0 (noise) to 1 (clean prose). Empty text scores 0."""
    tokens = _TOKEN.findall(text or "")
    if not tokens:
        return 0.0
    words = sum(1 for t in tokens if _WORD.match(t))
    stray = len(_STRAY.findall(text)) / max(1, len(text))
    return round(max(0.0, words / len(tokens) - 4 * stray), 3)


READABLE = 0.6  # below this a passage is not shown as a quote


def is_readable(text: str | None) -> bool:
    return readability(text) >= READABLE
