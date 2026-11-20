"""Private material in memory only: salted fingerprints out, nothing else.

    load_salt(env=None) -> Salt
    fingerprint(material, salt) -> "hmac256:<32 hex>"
    match_private_to_public(private_material, candidate_public_fingerprints, *, derive, salt) -> dict
    DerivationOptIn(fn, decision)
    with scrub(salt) as box: ...   box.put / box.fingerprint / box.match / box.emit

The salt is a secret file named by ``KAIRO_FINGERPRINT_SALT_FILE`` (default
``~/.kairo/fingerprint-salt``), refused in the same places as the GitHub token
(token.location_problem: inside a git working tree, an Obsidian vault or a synced
cloud folder). It must exist and hold at least 16 bytes: an unsalted fingerprint of
a short secret can be reversed by guessing, so there is no unsalted mode. With the
salt kept out of every repository and the vault, a published fingerprint cannot be
tested against a guess by anyone without the salt; with it, it can (keep it as
private as the token, and destroy it when the study's retention period ends).

``fingerprint`` = HMAC-SHA256(salt, material) truncated to 128 bits, hex.

``match_private_to_public``: does a private key found in a file correspond to a
public key published elsewhere? Answering it means DERIVING the public key from the
private one, which is a computation with the key. Whether that counts as "using" a
credential found in public is an ethical (and possibly legal) judgement this plugin
does not make: the derivation is never built in and never implicit. The caller passes
``derive=DerivationOptIn(fn, decision="...")``, where `fn(material) -> iterable of
public forms` (bytes / str, each fingerprinted with the same salt) and `decision`
names where the study recorded that choice (an ethics approval, a plan section). A bare
callable is refused. The result holds booleans, counts and fingerprints, plus the
decision text so the choice travels with every result.

``scrub`` (best effort, Python's limits apply): material handed to ``box.put`` is
copied into a bytearray that is overwritten with zeros when the block ends, and the
Sealed handle never prints, pickles or compares its content; an exception raised in
the block leaves it as ``ScrubbedError`` naming only the exception type (its message,
arguments and traceback frames, which may hold the material, are dropped);
``box.emit(obj)`` is the way out: it refuses anything that is not JSON made of
fingerprints, booleans, numbers and short identifier strings, or whose JSON contains
any held material (raw, hex or base64). Limits: Python cannot erase immutable
``bytes`` / ``str`` objects (the caller's own copies, decoded strings, slices), the
garbage collector decides when memory is reused, and swap, core dumps and debuggers
see process memory. ``scrub`` reduces accidental persistence (logs, exceptions,
reprs, results); it is not a guarantee against an attacker with access to the process.
Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from .token import location_problem

ENV_FILE = "KAIRO_FINGERPRINT_SALT_FILE"
DEFAULT_FILE = Path("~/.kairo/fingerprint-salt")
MIN_SALT = 16
FP_PREFIX = "hmac256:"
_FP_RE = re.compile(r"^hmac256:[0-9a-f]{32}$")
_SAFE_STR = re.compile(r"^[A-Za-z0-9_.:\-/ ]{0,64}$")


class SaltRefused(Exception):
    """The salt file is missing, too short, or somewhere it must not be."""


class Salt:
    __slots__ = ("_value", "path")

    def __init__(self, value: bytes, path: Path | None = None):
        if not isinstance(value, (bytes, bytearray)) or len(value) < MIN_SALT:
            raise SaltRefused(f"la sal debe tener al menos {MIN_SALT} bytes")
        self._value = bytes(value)
        self.path = path

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return f"Salt(path={str(self.path)!r}, value=<oculto>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("la sal no se serializa")


def salt_path(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(os.path.expanduser(env.get(ENV_FILE) or str(DEFAULT_FILE)))


def load_salt(env: dict | None = None) -> Salt:
    env = os.environ if env is None else env
    path = salt_path(env)
    problem = location_problem(path, env)
    if problem:
        raise SaltRefused(problem.replace("del token", "de la sal").replace("~/.kairo/github-token",
                                                                             "~/.kairo/fingerprint-salt"))
    if not path.is_file():
        raise SaltRefused(f"no hay sal en {path}: crea un fichero con al menos {MIN_SALT} bytes aleatorios "
                          f"fuera de repositorios y del vault, o define {ENV_FILE}")
    try:
        raw = path.read_bytes().rstrip(b"\r\n")
    except OSError:
        raise SaltRefused(f"no se puede leer {path}") from None
    return Salt(raw, path)


def _bytes(material) -> bytes:
    if isinstance(material, Sealed):
        return bytes(material._buf)
    if isinstance(material, str):
        return material.encode("utf-8")
    if isinstance(material, (bytes, bytearray, memoryview)):
        return bytes(material)
    raise TypeError("el material debe ser bytes o str")


def _salt_bytes(salt) -> bytes:
    if isinstance(salt, Salt):
        return salt.reveal()
    if isinstance(salt, (bytes, bytearray)) and len(salt) >= MIN_SALT:
        return bytes(salt)
    raise SaltRefused(f"hace falta una sal (load_salt) de al menos {MIN_SALT} bytes")


def fingerprint(material, salt) -> str:
    """HMAC-SHA256(salt, material), first 128 bits, as `hmac256:<32 hex>`."""
    return FP_PREFIX + hmac.new(_salt_bytes(salt), _bytes(material), hashlib.sha256).hexdigest()[:32]


class DerivationOptIn:
    """The researcher's explicit choice to derive public forms from private material."""

    __slots__ = ("fn", "decision")

    def __init__(self, fn, decision: str):
        if not callable(fn):
            raise TypeError("fn debe ser una función material -> formas públicas")
        if not isinstance(decision, str) or len(decision.strip()) < 10:
            raise ValueError("decision debe decir dónde quedó registrada la decisión ética (p. ej. la sección "
                             "del plan o la aprobación del comité)")
        self.fn = fn
        self.decision = decision.strip()

    def __repr__(self) -> str:
        return f"DerivationOptIn(decision={self.decision!r})"


def match_private_to_public(private_material, candidate_public_fingerprints, *, derive, salt) -> dict:
    """Whether a public form derived from `private_material` is among the candidates.

    Returns {"matched": bool, "matches": [fingerprints], "derived": n, "decision": text}."""
    if not isinstance(derive, DerivationOptIn):
        raise TypeError("derive debe ser DerivationOptIn(fn, decision=...): derivar la clave pública es "
                        "una decisión del estudio, no del plugin")
    cands = set(candidate_public_fingerprints or ())
    for c in cands:
        if not isinstance(c, str) or not _FP_RE.match(c):
            raise ValueError("los candidatos deben ser huellas hmac256:<32 hex> hechas con la misma sal")
    raw = _bytes(private_material)
    failed = None
    try:
        forms = list(derive.fn(raw) or ())
    except Exception as e:  # noqa: BLE001 — never let the material ride on an exception
        failed = type(e).__name__
        _strip(e)
    if failed:
        raise ScrubbedError(failed)         # outside the handler: nothing chained
    fps = [fingerprint(f, salt) for f in forms]
    hits = sorted({f for f in fps if f in cands})
    return {"matched": bool(hits), "matches": hits, "derived": len(fps), "decision": derive.decision}


class ScrubbedError(Exception):
    """An exception raised while handling private material, stripped to its type name."""


class Sealed:
    """A handle on material held by a Scrubber: no repr, str, pickling or equality of content."""

    __slots__ = ("_buf", "__weakref__")

    def __init__(self, data: bytes):
        self._buf = bytearray(data)

    def __repr__(self) -> str:
        return f"Sealed(<{len(self._buf)} bytes ocultos>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("el material no se serializa")

    def __len__(self) -> int:
        return len(self._buf)

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__

    def wipe(self) -> None:
        for i in range(len(self._buf)):
            self._buf[i] = 0


class OutboundRefused(ValueError):
    pass


def _check_value(v, depth=0) -> None:
    if depth > 8:
        raise OutboundRefused("demasiado anidado")
    if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
        return
    if isinstance(v, str):
        if _FP_RE.match(v) or _SAFE_STR.match(v):
            return
        raise OutboundRefused("solo salen huellas, booleanos, números y textos cortos de identificador")
    if isinstance(v, list):
        for x in v:
            _check_value(x, depth + 1)
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or not _SAFE_STR.match(k):
                raise OutboundRefused("clave no permitida")
            _check_value(x, depth + 1)
        return
    raise OutboundRefused(f"tipo no permitido: {type(v).__name__}")


class Scrubber:
    def __init__(self, salt):
        self._salt = salt
        self._held: list[Sealed] = []

    def put(self, material) -> Sealed:
        s = Sealed(_bytes(material))
        self._held.append(s)
        return s

    def fingerprint(self, sealed) -> str:
        return fingerprint(sealed, self._salt)

    def match(self, sealed, candidate_public_fingerprints, *, derive) -> dict:
        return match_private_to_public(sealed, candidate_public_fingerprints, derive=derive, salt=self._salt)

    def _forms(self, s: Sealed) -> list[bytes]:
        raw = bytes(s._buf)
        if len(raw) < 4:
            return []
        forms = [raw, raw.hex().encode(), base64.b64encode(raw), base64.urlsafe_b64encode(raw).rstrip(b"=")]
        try:
            forms.append(raw.decode("utf-8").encode("unicode_escape"))
        except UnicodeDecodeError:
            pass
        return [f for f in forms if len(f) >= 4]

    def emit(self, obj):
        """`obj` if it may leave the block (see the module docstring), else OutboundRefused."""
        _check_value(obj)
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        for s in self._held:
            if any(f in text for f in self._forms(s)):
                raise OutboundRefused("la salida contiene material retenido")
        return obj

    def wipe(self) -> None:
        for s in self._held:
            s.wipe()
        self._held.clear()


def _strip(e: BaseException) -> None:
    """Empty an exception that may quote the material: args, attributes, frames, chain."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        nxt = e.__context__ or e.__cause__
        try:
            e.args = ()
        except (AttributeError, TypeError):
            pass
        for attr in ("object", "filename", "filename2", "doc", "msg", "text", "reason", "start", "end"):
            try:
                if hasattr(e, attr):
                    setattr(e, attr, None)
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            e.__dict__.clear()
        except AttributeError:
            pass
        e.__traceback__ = None
        e.__context__ = e.__cause__ = None
        e = nxt


@contextmanager
def scrub(salt):
    box = Scrubber(salt)
    failed = None
    try:
        yield box
    except (ScrubbedError, OutboundRefused):
        raise
    except Exception as e:  # noqa: BLE001 — re-raised below, stripped of message and frames
        failed = type(e).__name__
        _strip(e)
    finally:
        box.wipe()
    if failed:
        # contextlib throws the block's exception into this generator, so Python still
        # chains it as __context__: it was stripped above and is hidden (`from None`)
        raise ScrubbedError(failed) from None
