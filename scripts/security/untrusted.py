#!/usr/bin/env python3
"""Flag third-party text (an abstract, a paper section) that reads like an
instruction to a model — the fingerprint of a prompt injection.

    from untrusted import suspicious
    suspicious("… Ignore all previous instructions and …")  # -> ["ignore … instructions"]

A heuristic, not a security boundary: it marks text so the reader treats it
as data and reports it; it never removes or rewrites anything. Standard
library only.
"""

from __future__ import annotations

import re

_IMPERATIVE = r"(?:^|[.!?]\s+|\bplease\s+|\byou\s+(?:must|should|need\s+to)\s+|\bnow\s+)"
_PATTERNS = (
    ("ignore … instructions", r"\b(?:ignore|disregard|forget)\b[^.]{0,40}\b(?:previous|prior|above|earlier|all)\b"
                              r"[^.]{0,30}\b(?:instructions?|prompts?|rules?|context)\b"),
    ("you are now / act as", r"\b(?:you are now|from now on,? you|act as (?:an?|the) )"),
    ("system prompt", r"\b(?:system prompt|developer message|system message)\b"),
    # A request is an imperative: at the start of a sentence, or after "please" / "you must".
    # "We run the benchmark script" or "download the artifact at https://…" in a systems
    # abstract describes the work; it does not ask the reader to do anything.
    ("tool / command request", _IMPERATIVE + r"(?:run|execute|call)\b[^.]{0,30}\b(?:command|shell|bash|tool|script)\b"),
    ("fetch / open request", _IMPERATIVE + r"(?:fetch|download|open|visit)\b[^.]{0,20}\bhttps?://"),
    ("reviewer / model address", r"\b(?:as an? (?:ai|llm|language model)|dear (?:ai|llm|assistant|model)"
                                 r"|(?:llm|ai) reviewers?)\b"),
    ("hidden instruction markup", r"<\s*/?\s*(?:system|instructions?|prompt)\s*>"),
)
_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _PATTERNS]


def suspicious(text: str | None) -> list[str]:
    """Names of the instruction-like patterns found in `text` (empty: none)."""
    if not text:
        return []
    return [name for name, rx in _COMPILED if rx.search(text)]
