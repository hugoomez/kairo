"""Whether the second critic (a different model family on DeepInfra) may run.

One switch for the whole plugin: ``KAIRO_SECOND_CRITIC=on`` *and*
``DEEPINFRA_TOKEN`` set. Anything else means it is not used, and every caller
records it as «no disponible» with the reason, never as an error and never as
agreement.

    python status.py        -> {"available": false, "state": "desactivado", "reason": "..."}

Standard library only.
"""
from __future__ import annotations

import json
import os


def status(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    switch = (env.get("KAIRO_SECOND_CRITIC") or "").strip().lower()
    if switch != "on":
        return {"available": False, "state": "desactivado",
                "reason": "desactivado por elección (KAIRO_SECOND_CRITIC no está en «on»)"}
    if not (env.get("DEEPINFRA_TOKEN") or "").strip():
        return {"available": False, "state": "sin_token",
                "reason": "KAIRO_SECOND_CRITIC=on pero falta DEEPINFRA_TOKEN"}
    return {"available": True, "state": "activo", "reason": "KAIRO_SECOND_CRITIC=on y DEEPINFRA_TOKEN presente"}


def label(st: dict) -> str:
    return "disponible" if st["available"] else f"no disponible — {st['reason']}"


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False))
