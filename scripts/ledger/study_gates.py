"""Puertas de comprobación de los diseños de estudio.

Cada una es `check(vault, node_path, fm) -> list[str]`: lo que falta (vacía = pasa).
Stubs hasta la Task 2.3.
"""
from __future__ import annotations


def _pending(vault, node_path, fm) -> list[str]:
    return ["sin implementar"]


GATES = {
    "plan_frozen": _pending,
    "estimates_reproducible": _pending,
    "classifiers_validated": _pending,
    "verifier_no_errors": _pending,
    "avisos_cerrados": _pending,
}
