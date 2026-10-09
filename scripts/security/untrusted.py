#!/usr/bin/env python3
"""Flag third-party text (an abstract, a paper section) that reads like an
instruction to a model — the fingerprint of a prompt injection.

    from untrusted import suspicious
    suspicious("… Ignore all previous instructions and …")  # -> ["ignore … instructions"]

A heuristic, not a security boundary: it marks text so the reader treats it
as data and reports it; it never removes or rewrites anything. The text is
matched after NFKC folding with zero-width and bidi-control characters taken
out (`ig​nore` reads as `ignore`); an instruction written with them also
gets `caracteres invisibles` (alone they are common — soft hyphens — and not
marked). Besides English, the commonest phrasings in Spanish, French,
German, Portuguese, Italian and Chinese are recognised — still a short list,
never a guarantee. Standard library only.
"""

from __future__ import annotations

import re
import unicodedata

# zero-width spaces / joiners, word joiner, BOM, soft hyphen, and the bidi controls
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")

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
    # other languages: "ignore the previous instructions", "you are now", "system prompt"
    ("ignore … instructions (es/pt/it)",
     r"\b(?:ignora|ignore|ignorar|olvida|esquece|dimentica|descarta)\b[^.]{0,40}"
     r"\b(?:instrucciones|instruções|instruzioni|indicaciones|reglas|regras|regole)\b"),
    # a French article after the verb, so the English "ignore … instructions" is not counted twice
    ("ignore … instructions (fr)", r"\b(?:ignore[zr]?|oublie[zr]?)\s+(?:toutes\s+)?(?:les|tes|vos)\b[^.]{0,30}"
                                   r"\b(?:instructions|consignes|règles)\b"),
    ("ignore … instructions (de)", r"\b(?:ignorier(?:e|en)?|vergiss)\b[^.]{0,40}"
                                   r"\b(?:anweisungen|instruktionen|regeln)\b"),
    ("ignore … instructions (zh)", r"(?:忽略|无视|忽视)[^。]{0,20}(?:指令|指示|提示|规则)"),
    ("you are now (es/pt/fr/it/de)", r"\b(?:ahora eres|a partir de ahora,? (?:eres|debes)|agora você é|"
                                     r"tu es maintenant|désormais,? tu|ora sei|du bist jetzt|ab jetzt bist du)\b"),
    ("system prompt (es/pt/fr/it/de/zh)", r"(?:\bprompt del sistema\b|\bprompt do sistema\b|\binvite système\b|"
                                          r"\bprompt di sistema\b|\bsystemaufforderung\b|\bsystem-prompt\b|系统提示)"),
    ("model address (es/pt/fr/it/de)", r"\b(?:como (?:una? )?(?:ia|modelo de lenguaje)|"
                                       r"querid[oa] (?:ia|modelo|asistente)|revisores? (?:llm|ia)|"
                                       r"en tant qu'(?:ia|assistant)|als (?:ki|sprachmodell))\b"),
)
_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _PATTERNS]


def fold(text: str) -> str:
    """The text as a reader sees it: NFKC (full-width and compatibility forms
    folded) with zero-width and bidi-control characters removed."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


def suspicious(text: str | None) -> list[str]:
    """Names of the instruction-like patterns found in `text` (empty: none)."""
    if not text:
        return []
    flat = fold(text)
    found = [name for name, rx in _COMPILED if rx.search(flat)]
    if _INVISIBLE.search(text) and found:
        found.append("caracteres invisibles")      # an instruction split by invisible characters
    return found
