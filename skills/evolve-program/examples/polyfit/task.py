"""Example frozen task for evolve-program — hidden-polynomial fit, known optimum.

The evolved program implements ``solve(x: float) -> float``. Targets are
y = 3x^2 - 2x + 1 (never shown to the program). Score = 1 / (1 + MSE), in (0, 1];
the known optimum is exactly 1.0 (MSE 0). The held-out split uses x values the
train split never contains, so a program that memorises train pairs instead of
the function scores high on train and low on held-out.
"""
import math

MAX_ABS_OUTPUT = 1e6


def check_output(outputs, inputs):
    """Shape/bounds validation. Return None if fine, else a reason string."""
    for i, o in enumerate(outputs):
        if isinstance(o, bool) or not isinstance(o, (int, float)):
            return f"item {i} is {type(o).__name__}, not a number"
        if not math.isfinite(o) or abs(o) > MAX_ABS_OUTPUT:
            return f"item {i} = {o!r} out of bounds (|y| <= {MAX_ABS_OUTPUT})"
    return None


def score(outputs, targets):
    mse = sum((o - t) ** 2 for o, t in zip(outputs, targets)) / len(targets)
    return 1.0 / (1.0 + mse)
