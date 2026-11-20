"""Tests for kairo_collect.sensitive. Material comes from the synthetic corpus
(scripts/security/fixtures_corpus.py: invented, never real keys); salt files live under tmp_path."""
from __future__ import annotations

import json
import pickle
import random
import sys
import traceback
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import fixtures_corpus as fc  # noqa: E402
from kairo_collect import sensitive as sv  # noqa: E402


def _salt_file(d: Path, data: bytes = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "fingerprint-salt"
    p.write_bytes(data if data is not None else bytes(random.Random(7).getrandbits(8) for _ in range(32)))
    return p


def _identity(seed: int) -> tuple[str, str]:
    """(AGE-SECRET-KEY-1..., age1...) from the corpus generator: invented, pattern-valid."""
    return fc.x25519_identity(fc.Gen(seed))


def derive_age_recipient(material: bytes):
    """The opt-in derivation used by these tests: X25519 public key of an invented age identity."""
    dec = fc.bech32_decode(material.decode("ascii"))
    if dec is None:
        return []
    return [fc.bech32_encode("age", fc.x25519_base(dec[1]))]


OPT_IN = sv.DerivationOptIn(derive_age_recipient, decision="plan del estudio inventado, sección 4.2 (prueba)")


def test_salt_location_and_content_rules(tmp_path):
    ok = _salt_file(tmp_path / "home" / ".kairo")
    salt = sv.load_salt({sv.ENV_FILE: str(ok)})
    assert "oculto" in repr(salt) and salt.reveal() not in repr(salt).encode()
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    for p in (_salt_file(tmp_path / "repo" / "conf"), _salt_file(tmp_path / "OneDrive - Inventada" / "k")):
        with pytest.raises(sv.SaltRefused):
            sv.load_salt({sv.ENV_FILE: str(p)})
    with pytest.raises(sv.SaltRefused):
        sv.load_salt({sv.ENV_FILE: str(tmp_path / "nada")})
    with pytest.raises(sv.SaltRefused):
        sv.load_salt({sv.ENV_FILE: str(_salt_file(tmp_path / "corta", b"corta"))})
    with pytest.raises(TypeError):
        pickle.dumps(salt)


def test_fingerprint_is_salted_and_truncated(tmp_path):
    a, b = b"s" * 32, b"t" * 32
    secret, _ = _identity(1)
    fa, fb = sv.fingerprint(secret, a), sv.fingerprint(secret, b)
    assert fa != fb and fa.startswith("hmac256:") and len(fa) == len("hmac256:") + 32
    assert sv.fingerprint(secret.encode(), a) == fa
    assert secret not in fa
    with pytest.raises(sv.SaltRefused):
        sv.fingerprint(secret, b"")


def test_match_needs_an_explicit_opt_in_and_finds_the_pair():
    salt = b"x" * 32
    secret, recipient = _identity(2)
    _, other = _identity(3)
    cands = {sv.fingerprint(recipient, salt), sv.fingerprint(other, salt)}
    with pytest.raises(TypeError):
        sv.match_private_to_public(secret, cands, derive=derive_age_recipient, salt=salt)
    with pytest.raises(ValueError):
        sv.DerivationOptIn(derive_age_recipient, decision="sí")
    res = sv.match_private_to_public(secret, cands, derive=OPT_IN, salt=salt)
    assert res["matched"] and res["matches"] == [sv.fingerprint(recipient, salt)] and res["derived"] == 1
    assert res["decision"].startswith("plan del estudio")
    assert secret not in json.dumps(res) and recipient not in json.dumps(res)
    miss = sv.match_private_to_public(secret, {sv.fingerprint(other, salt)}, derive=OPT_IN, salt=salt)
    assert not miss["matched"]
    with pytest.raises(ValueError):
        sv.match_private_to_public(secret, {recipient}, derive=OPT_IN, salt=salt)   # raw keys are not fingerprints


def test_scrub_wipes_emits_only_fingerprints_and_strips_exceptions():
    salt = b"y" * 32
    secret, recipient = _identity(4)
    cands = {sv.fingerprint(recipient, salt)}
    with sv.scrub(salt) as box:
        h = box.put(secret)
        assert secret not in repr(h) and secret not in str(h)
        with pytest.raises(TypeError):
            pickle.dumps(h)
        res = box.emit({"fp": box.fingerprint(h), "match": box.match(h, cands, derive=OPT_IN)["matched"], "n": 1})
        for leak in ({"raw": secret}, {"hex": secret.encode().hex()}, {"note": "texto libre con espacios y más"}):
            with pytest.raises(sv.OutboundRefused):
                box.emit(leak)
    assert res["match"] is True and bytes(h._buf) == b"\x00" * len(secret)

    with pytest.raises(sv.ScrubbedError) as e:
        with sv.scrub(salt) as box:
            box.put(secret)
            raise ValueError(f"no se pudo leer {secret}")
    text = "".join(traceback.format_exception(e.value))
    assert secret not in text and "ValueError" in str(e.value)
    assert e.value.__suppress_context__ and e.value.__cause__ is None
    ctx = e.value.__context__                     # contextlib chains it; it was emptied first
    assert ctx is None or (secret not in repr(ctx) and ctx.__traceback__ is None and ctx.args == ())

    def broken(material):
        raise UnicodeDecodeError("utf-8", material, 0, 1, "inventado")
    with pytest.raises(sv.ScrubbedError) as e2:
        sv.match_private_to_public(secret, set(), derive=sv.DerivationOptIn(broken, "plan inventado, sección 9"),
                                   salt=salt)
    assert e2.value.__context__ is None and "UnicodeDecodeError" in str(e2.value)


def test_corpus_private_files_fingerprint_without_leaking(tmp_path):
    salt = b"z" * 32
    privs = [f for f in fc.build(11, 1) if f.has_private_material]
    assert privs
    out = []
    with sv.scrub(salt) as box:
        for f in privs:
            out.append(box.emit({"tool": f.tool, "fp": box.fingerprint(box.put(f.data))}))
    blob = json.dumps(out)
    for f in privs:
        assert f.data.decode("latin-1")[:40] not in blob
