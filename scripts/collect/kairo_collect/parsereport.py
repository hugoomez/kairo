"""Parse report: what a parser made of every file, without any of the files' values.

A per-format parser of the study calls, once per file::

    from kairo_collect.parsereport import ParseReport, skeleton
    with ParseReport(out_dir, dataset="repos") as report:
        for rel in files:                       # rel: path relative to the dataset folder
            try:
                facts = parse(text)
                report.ok(rel, "sops-yaml", "sops")
            except ParseError as e:
                report.fail(rel, "sops-yaml", "missing_mac", skeleton(text))
            ...
            report.unknown(rel, "yaml-no-sops")

Files written in `out_dir`:

    parse-report.jsonl   one line per call:
        {"item": "<path relative to the registered dataset folder, posix>",
         "status": "ok" | "fail" | "unknown", "format": str|null, "tool": str|null,
         "error_class": str|null, "hint": str|null, "skeleton": str|null}
        The item is the file's REAL relative path (owner/repo/... in a collected
        tree): debug_sample.py --from-report maps it back to the file. Keep this file
        where the dataset is (a sensitive location), not where an agent reads.
    parse-summary.json   counts only, rewritten by close():
        {"schema": "kairo/parse-summary@1", "dataset", "items", "by_status": {...},
         "by_tool": {tool: {status: n}}, "by_format": {format: {status: n}},
         "by_error_class": {class: n}, "by_format_error_class": {format: {class: n}},
         "coverage": {"ok", "seen", "pct_of_seen", "expected", "pct_of_expected"}}
        An item reported twice counts once, with its last status.

`fmt`, `tool`, `error_class` and `hint` are short identifiers (letters, digits,
`_ . + - /`, up to 64 characters): an exception's message is not an error class and
is refused (ValueError), since it may quote the input.

``skeleton(text)``: a structural outline of the input for
debugging the parser on what failed: strict_redact first, then every value replaced
by a type token (``<str>``, ``<int>``, ``<b64:44>``, ``<hex:64>``, ``<sops-enc:AES256_GCM:str>``,
``<age-recipient>``, ``<redacted:env_value>``, ...), keeping indentation, nesting,
punctuation, key names that look like identifiers (others become ``<key>``),
comment positions (``# <comment>``), armor labels and known format words (age
stanza types, ssh key types). Repeated identical lines collapse to ``... ×N``. It never
holds a value; it is idempotent (``skeleton(skeleton(x)) == skeleton(x)``), so
``fail`` re-skeletonises whatever it is given. Output is capped at SKELETON_MAX_LINES
lines and SKELETON_MAX_CHARS characters.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

from .archive import atomic_write_json

_SECURITY = Path(__file__).resolve().parents[2] / "security"
SCHEMA = "kairo/parse-summary@1"
REPORT, SUMMARY = "parse-report.jsonl", "parse-summary.json"
SKELETON_MAX_LINES = 200
SKELETON_MAX_CHARS = 8000
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-/]{0,63}$")


def _sr():
    if str(_SECURITY) not in sys.path:
        sys.path.insert(0, str(_SECURITY))
    import strict_redact  # scripts/security/strict_redact.py
    return strict_redact


# ---- skeleton -------------------------------------------------------------------

_TOKEN_KINDS = ("str", "int", "float", "bool", "null", "date", "datetime", "uuid", "url", "email", "path",
                "b64", "hex", "word", "key", "comment", "age-recipient", "dotenvx-enc", "sops-enc", "redacted",
                "ssh-pubkey-body", "armor-body", "binary", "truncated", "more-lines")
_SOPS_ALGS = {"AES256_GCM"}
_SOPS_TYPES = {"str", "int", "float", "bool", "bytes", "comment"}
_TOKEN_RE = re.compile(r"<(?:(?:" + "|".join(re.escape(k) for k in _TOKEN_KINDS if k not in ("sops-enc", "redacted"))
                       + r")(?::\d{1,6})?|sops-enc(?::AES256_GCM:(?:str|int|float|bool|bytes|comment))?"
                       + r"|redacted:[a-z_]{1,30})>")
_MARK_RE = re.compile(r"\[REDACTADO ([A-Za-z0-9_]+)[^\]\n]*\]")
_SHIELD_RE = re.compile(r"\[REDACTADO kskel sha256:([0-9a-f]{12})\]")
_ARMOR_LABELS = {"AGE ENCRYPTED FILE", "PGP MESSAGE", "PGP PUBLIC KEY BLOCK", "PGP PRIVATE KEY BLOCK", "CERTIFICATE",
                 "PUBLIC KEY", "RSA PUBLIC KEY", "PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY",
                 "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY", "PGP SIGNATURE"}
_ARMOR_RE = re.compile(r"^-----(BEGIN|END) ([A-Z0-9 ]{1,40})-----$")
# words that are format structure, not values: kept as they are
VOCAB = {"age-encryption.org/v1", "->", "---", "X25519", "scrypt", "ssh-ed25519", "ssh-rsa", "ssh-dss",
         "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "mlkem768x25519", "p256tag",
         "mlkem768p256tag", "piv-p256", "AES256_GCM", "true", "false", "null", "|", ">", "|-", ">-", "|+", ">+",
         "...", "-", "[", "]", "{", "}", ",",
         # public format values a parser branches on (Kubernetes kinds and API versions)
         "Secret", "SealedSecret", "ConfigMap", "Opaque", "v1", "bitnami.com/v1alpha1"}
_B64_LINE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")
_QSTR = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_KEY_LINE = re.compile(r"^(?P<lead>-[ \t]+)?(?:(?P<export>export)[ \t]+)?"
                       r"(?P<key>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|[^\s:=#\"'{}\[\],]+)"
                       r"(?P<sep>[ \t]*(?::(?=[ \t]|$)|=)[ \t]*)(?P<rest>.*)$")
_PUNCT_ONLY = re.compile(r"^[\-=|>.~*_#+:]+$")
_FLOW_WORD = re.compile(r"<[^<>\s]{1,40}>|\[REDACTADO [^\]\n]*\]|[^{}\[\],:\s][^{}\[\],\s]*?(?=[{}\[\],\s]|:\s|:$|$)")
_COUNT_SUFFIX = re.compile(r"^(.*) ×(\d{1,7})$")


def _key_ok(k: str) -> bool:
    if not re.fullmatch(r"[A-Za-z_$@][A-Za-z0-9_.\-/$@]{0,63}", k):
        return False
    sr = _sr()
    if sr.hot_token(k) or "@" in k[1:] or re.search(r"\d{5,}", k):
        return False
    return True


def _key(k: str) -> str:
    if len(k) >= 2 and k[0] == k[-1] and k[0] in "\"'":
        inner = k[1:-1]
        return k[0] + (inner if _key_ok(inner) or _TOKEN_RE.fullmatch(inner) else "<key>") + k[0]
    if _TOKEN_RE.fullmatch(k):
        return k
    return k if _key_ok(k) else "<key>"


def value_shape(v: str) -> str:
    """The type token of one scalar value (already strict-redacted)."""
    s = v.strip()
    if not s:
        return s
    if _TOKEN_RE.fullmatch(s) or s in VOCAB:
        return s
    m = _MARK_RE.fullmatch(s)
    if m:
        kind = re.sub(r"[^a-z_]", "", m.group(1).lower())[:30] or "x"
        return f"<redacted:{kind}>"
    if s[0] in "\"'" and len(s) >= 2 and s[-1] == s[0]:
        inner = value_shape(s[1:-1])
        return s[0] + inner + s[0]
    low = s.lower()
    if low in ("true", "false", "yes", "no", "on", "off"):
        return "<bool>"
    if low in ("null", "~", "none", "nil"):
        return "<null>"
    if re.fullmatch(r"[+\-]?\d+", s):
        return "<int>"
    if re.fullmatch(r"[+\-]?(?:\d+\.\d*|\.\d+)(?:[eE][+\-]?\d+)?", s):
        return "<float>"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return "<date>"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?", s):
        return "<datetime>"
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s):
        return "<uuid>"
    if s.startswith("ENC[") and s.endswith("]"):
        m = re.search(r"^ENC\[([A-Za-z0-9_]+),", s)
        t = re.search(r"type:([a-z]+)\]$", s)
        alg = m.group(1) if m and m.group(1) in _SOPS_ALGS else None
        typ = t.group(1) if t and t.group(1) in _SOPS_TYPES else None
        return f"<sops-enc:{alg}:{typ}>" if alg and typ else "<sops-enc>"
    if s.startswith("encrypted:"):
        return "<dotenvx-enc>"
    if re.fullmatch(r"age1[0-9a-z]{20,}", s):
        return "<age-recipient>"
    if re.match(r"^[a-z][a-z0-9+.\-]*://", s):
        return "<url>"
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", s):
        return "<email>"
    if re.fullmatch(r"[0-9a-fA-F]{8,}", s) and len(s) % 2 == 0:
        return f"<hex:{len(s)}>"
    if re.fullmatch(r"[A-Za-z0-9+/_\-]{16,}={0,2}", s) and re.search(r"\d", s) and re.search(r"[A-Za-z]", s):
        return f"<b64:{len(s)}>"
    if "/" in s and not re.search(r"\s", s):
        return "<path>"
    return "<str>"


def _words(rest: str) -> str:
    """A run of free text: quoted strings and words become tokens, punctuation stays."""
    out, pos = [], 0
    for m in re.finditer(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|<[^<>\s]{1,40}>|\[REDACTADO [^\]\n]*\]|[^\s,]+",
                         rest):
        out.append(rest[pos:m.start()])
        w = m.group(0)
        if w in VOCAB or _PUNCT_ONLY.fullmatch(w):
            out.append(w)
        else:
            tok = value_shape(w)
            out.append("<word>" if tok == "<str>" else tok)
        pos = m.end()
    out.append(rest[pos:])
    return "".join(out)


def _flow(s: str) -> str:
    """JSON / YAML flow collections: `{"k": v, ...}` `[a, b]`."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c in "{}[],: \t":
            out.append(c)
            i += 1
            continue
        m = _QSTR.match(s, i)
        if m:
            j = m.end()
            k = j
            while k < len(s) and s[k] in " \t":
                k += 1
            is_key = k < len(s) and s[k] == ":"
            out.append(_key(m.group(0)) if is_key else value_shape(m.group(0)))
            i = j
            continue
        m = _FLOW_WORD.match(s, i)
        if not m:
            out.append("<str>")
            break
        w = m.group(0)
        k = m.end()
        while k < len(s) and s[k] in " \t":
            k += 1
        is_key = k < len(s) and s[k] == ":"
        out.append(_key(w) if is_key else value_shape(w))
        i = m.end()
    return "".join(out)


def _split_comment(rest: str) -> tuple[str, str]:
    """(value, comment marker) for a YAML/INI/env value with an inline ` # comment`."""
    in_q = None
    for i, c in enumerate(rest):
        if in_q:
            if c == in_q:
                in_q = None
            continue
        if c in "\"'" and (i == 0 or rest[i - 1] in " \t:=[{,"):
            in_q = c
        elif c == "#" and i > 0 and rest[i - 1] in " \t":
            return rest[:i].rstrip(), " # <comment>"
    return rest, ""


def _value_part(rest: str) -> str:
    rest, comment = _split_comment(rest)
    trail = ""
    if rest.endswith(",") and not rest.endswith(",,"):
        rest, trail = rest[:-1], ","
    s = rest.strip()
    if not s:
        return rest + trail + comment
    if _TOKEN_RE.fullmatch(s):
        return s + trail + comment
    if s[0] in "{[":
        return _flow(s) + trail + comment
    if _TOKEN_RE.search(s) and re.search(r"\s", s):
        return _words(s) + trail + comment
    return value_shape(s) + trail + comment


def _line(ln: str) -> str:
    c = _COUNT_SUFFIX.match(ln.rstrip("\r"))
    if c:                                     # a line already collapsed by an earlier pass
        return f"{_line(c.group(1))} ×{c.group(2)}"
    body = ln.rstrip("\r")
    stripped = body.lstrip(" \t")
    ind = body[:len(body) - len(stripped)]
    if not stripped:
        return ind
    if stripped.startswith(("#", ";")) or stripped.startswith("//"):
        mark = stripped[0] if stripped[0] != "/" else "//"
        return ind + mark + (" <comment>" if stripped[len(mark):].strip() else "")
    m = _ARMOR_RE.match(stripped)
    if m:
        label = m.group(2) if m.group(2) in _ARMOR_LABELS else "<armor>"
        return f"{ind}-----{m.group(1)} {label}-----"
    if stripped in VOCAB or _TOKEN_RE.fullmatch(stripped):
        return ind + stripped
    if _B64_LINE.match(stripped):             # a base64 body line (a padded one looks like `x=`)
        return ind + value_shape(stripped)
    sec = re.fullmatch(r"\[([^\]\n]{1,80})\]", stripped)
    if sec and not stripped.startswith("[REDACTADO"):
        inner = sec.group(1).strip()
        return f"{ind}[{inner if _key_ok(inner) or _TOKEN_RE.fullmatch(inner) else '<key>'}]"
    if stripped[0] in "{}[]":
        return ind + _flow(stripped)
    km = _KEY_LINE.match(stripped)
    if km and not stripped.startswith("->"):
        lead = km.group("lead") or ""
        exp = (km.group("export") + " ") if km.group("export") else ""
        return ind + lead + exp + _key(km.group("key")) + km.group("sep") + _value_part(km.group("rest"))
    if stripped.startswith("- "):
        return ind + "- " + _value_part(stripped[2:])
    return ind + _words(stripped)


def skeleton(text) -> str:
    """The structural outline of `text` (bytes are decoded as UTF-8; binary → `<binary:N>`).

    strict_redact runs without a file-name hint: its env-file rule would turn a second
    pass's `KEY=<dotenvx-enc>` into `<redacted:env_value>`. Every value becomes a token
    anyway; the strict pass is the backstop for material in free text and key names."""
    if isinstance(text, (bytes, bytearray)):
        raw = bytes(text)
        if raw.startswith(b"age-encryption.org/v1\n"):
            end = raw.find(b"\n---")
            nl = raw.find(b"\n", end + 1) if end >= 0 else -1
            if nl > 0:
                try:
                    head = raw[:nl + 1].decode("ascii")
                except UnicodeDecodeError:
                    head = None
                if head is not None:
                    return skeleton(head) + f"\n<binary:{len(raw) - nl - 1}>"
        if b"\x00" in raw:
            return f"<binary:{len(raw)}>"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary:{len(raw)}>"
    if not isinstance(text, str):
        raise TypeError("skeleton: text debe ser str o bytes")
    # tokens of an earlier pass are shielded as redaction markers, which strict_redact
    # leaves alone (idempotence: a `kind: Secret` value token is not redacted again)
    saved: list[str] = []

    def shield(m: re.Match) -> str:
        saved.append(m.group(0))
        return f"[REDACTADO kskel sha256:{len(saved) - 1:012x}]"

    def unshield(m: re.Match) -> str:
        i = int(m.group(1), 16)
        return saved[i] if i < len(saved) else "<redacted:x>"

    redacted, _ = _sr().redact_strict(_TOKEN_RE.sub(shield, text), "")
    redacted = _SHIELD_RE.sub(unshield, redacted)
    redacted = _MARK_RE.sub(lambda m: value_shape(m.group(0)), redacted)   # drops the markers' hashes
    lines = [_line(ln) for ln in redacted.split("\n")]
    out: list[str] = []
    prev, n = None, 0
    for ln in lines + [None]:
        if ln == prev:
            n += 1
            continue
        if prev is not None:
            out.append(prev if n == 1 else f"{prev} ×{n}")
        prev, n = ln, 1
    # an already collapsed line (`x ×3`) keeps its count on a second pass
    out = [re.sub(r"^(.*) ×(\d+) ×(\d+)$", lambda m: f"{m.group(1)} ×{int(m.group(2)) * int(m.group(3))}", x)
           for x in out]
    if len(out) > SKELETON_MAX_LINES:
        out = out[:SKELETON_MAX_LINES] + [f"<more-lines:{len(out) - SKELETON_MAX_LINES}>"]
    s = "\n".join(out)
    if len(s) > SKELETON_MAX_CHARS:
        s = s[:SKELETON_MAX_CHARS].rsplit("\n", 1)[0] + "\n<truncated>"
    return s


# ---- the report -------------------------------------------------------------------

def check_item(item_id: str) -> str:
    p = str(item_id).replace("\\", "/")
    parts = PurePosixPath(p).parts
    if not p or p.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", p) or "\x00" in p or "\n" in p:
        raise ValueError("item_id debe ser la ruta relativa del fichero dentro de la carpeta del dataset")
    return "/".join(x for x in parts if x not in ("", "."))


def _ident(v, what: str, required: bool = True):
    if v is None and not required:
        return None
    if not isinstance(v, str) or not _IDENT.match(v):
        raise ValueError(f"{what} debe ser un identificador corto (letras, cifras, _ . + - /; ≤ 64), "
                         "no un mensaje de error ni un valor del fichero")
    return v


class ParseReport:
    def __init__(self, out_dir, *, dataset: str | None = None, expected: int | None = None, append: bool = False):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / REPORT
        self.dataset = dataset
        self.expected = expected
        if not append and self.path.exists():
            self.path.unlink()
        self._fh = self.path.open("a", encoding="utf-8", newline="\n")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _write(self, rec: dict) -> None:
        self._fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self._fh.flush()

    def ok(self, item_id: str, fmt: str, tool: str) -> None:
        self._write({"item": check_item(item_id), "status": "ok", "format": _ident(fmt, "fmt"),
                     "tool": _ident(tool, "tool"), "error_class": None, "hint": None, "skeleton": None})

    def fail(self, item_id: str, fmt: str, error_class: str, skeleton_or_text, *, tool: str | None = None) -> None:
        """`skeleton_or_text`: skeleton(text), or the text itself; it is (re)skeletonised here."""
        item = check_item(item_id)
        sk = skeleton(skeleton_or_text) if skeleton_or_text is not None else None
        self._write({"item": item, "status": "fail", "format": _ident(fmt, "fmt", required=False),
                     "tool": _ident(tool, "tool", required=False), "error_class": _ident(error_class, "error_class"),
                     "hint": None, "skeleton": sk})

    def unknown(self, item_id: str, hint: str | None = None) -> None:
        self._write({"item": check_item(item_id), "status": "unknown", "format": None, "tool": None,
                     "error_class": None, "hint": _ident(hint, "hint", required=False), "skeleton": None})

    def close(self) -> dict:
        if not self._fh.closed:
            self._fh.close()
        s = summarise(self.path, dataset=self.dataset, expected=self.expected)
        atomic_write_json(self.dir / SUMMARY, s)
        return s


def read_report(path) -> dict[str, dict]:
    """item → its LAST record (lines cut by an interruption are skipped)."""
    last: dict[str, dict] = {}
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no hay {REPORT} en {p.parent}")
    with p.open(encoding="utf-8") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("item"), str) and r.get("status") in ("ok", "fail", "unknown"):
                last[r["item"]] = r
    return last


def summarise(path, dataset: str | None = None, expected: int | None = None) -> dict:
    recs = read_report(path).values()
    by_status: Counter = Counter()
    by_tool: dict = {}
    by_fmt: dict = {}
    by_err: Counter = Counter()
    by_fmt_err: dict = {}
    for r in recs:
        st = r["status"]
        by_status[st] += 1
        if r.get("tool"):
            by_tool.setdefault(r["tool"], Counter())[st] += 1
        fmt = r.get("format") or ("(desconocido)" if st == "unknown" else "(sin formato)")
        by_fmt.setdefault(fmt, Counter())[st] += 1
        if st == "fail":
            ec = r.get("error_class") or "(sin clase)"
            by_err[ec] += 1
            by_fmt_err.setdefault(fmt, Counter())[ec] += 1
    seen = sum(by_status.values())
    ok = by_status.get("ok", 0)
    pct = lambda a, b: round(100 * a / b, 2) if b else None  # noqa: E731
    srt = lambda d: {k: dict(sorted(v.items())) for k, v in sorted(d.items())}  # noqa: E731
    return {"schema": SCHEMA, "dataset": dataset, "items": seen,
            "by_status": {k: by_status.get(k, 0) for k in ("ok", "fail", "unknown")},
            "by_tool": srt(by_tool), "by_format": srt(by_fmt), "by_error_class": dict(sorted(by_err.items())),
            "by_format_error_class": srt(by_fmt_err),
            "coverage": {"ok": ok, "seen": seen, "pct_of_seen": pct(ok, seen), "expected": expected,
                         "pct_of_expected": pct(ok, expected) if expected else None}}


def skeleton_groups(path, error_class: str, limit: int = 20) -> list[dict]:
    """The distinct skeletons of the items that failed with `error_class`, most frequent
    first: [{"count", "formats": [...], "skeleton"}]. No item id."""
    groups: dict[str, dict] = {}
    for r in read_report(path).values():
        if r["status"] != "fail" or r.get("error_class") != error_class or not r.get("skeleton"):
            continue
        sk = skeleton(r["skeleton"])          # re-skeletonised: a hand-edited report cannot leak
        g = groups.setdefault(sk, {"count": 0, "formats": set(), "skeleton": sk})
        g["count"] += 1
        if r.get("format"):
            g["formats"].add(r["format"])
    out = sorted(groups.values(), key=lambda g: (-g["count"], g["skeleton"]))[:limit]
    return [{"count": g["count"], "formats": sorted(g["formats"]), "skeleton": g["skeleton"]} for g in out]
