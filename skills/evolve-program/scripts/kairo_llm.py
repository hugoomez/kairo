"""OpenEvolve LLM client for evolve-program: Claude Code CLI, subscription only.

OpenEvolve's own ``provider: claude_code`` (openevolve/llm/claude_code.py,
checked at openevolve 0.3.2) runs ``claude -p`` with the parent environment
inherited, the prompt as an argv element, default tools/settings/MCP, and a
per-CALL ``--max-budget-usd`` only. This client keeps the same interface but:

- **Never an API key.** The child environment is scrubbed of every variable
  that would route the call off the subscription (ANTHROPIC_API_KEY,
  ANTHROPIC_AUTH_TOKEN, Bedrock / Vertex / Foundry switches). ``--bare`` is never
  passed: in bare mode the CLI authenticates *only* with an API key.
- **No tools, no settings, no MCP** (``--tools "" --setting-sources ""
  --strict-mcp-config``): the model can only answer with text. It cannot edit
  the frozen evaluator, read held-out data, or run anything.
- **Prompt on stdin**, not argv (Windows' 32 767-char command-line limit).
- **Run-level budget.** Every call's ``total_cost_usd`` (the CLI's list-price
  equivalent of the subscription quota used — ``costBasis: "list"``) is appended
  to a JSONL spend ledger; a call is refused once the ledger total reaches the
  run cap or the call cap. Worker processes race, so the overshoot is bounded
  by ``workers x per_call_cap``; evolve_run.py states that bound in its plan.

Configured through environment variables set by evolve_run.py (inherited by
OpenEvolve's spawned workers): KAIRO_EVOLVE_LEDGER, KAIRO_EVOLVE_RUN_BUDGET_USD,
KAIRO_EVOLVE_MAX_CALLS, KAIRO_EVOLVE_PER_CALL_USD, KAIRO_EVOLVE_SANDBOX.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from openevolve.llm.base import LLMInterface

# Any of these makes `claude` bill something other than the logged-in subscription.
OFF_SUBSCRIPTION_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_BEARER_TOKEN_BEDROCK",
)


class BudgetExhausted(RuntimeError):
    pass


def scrubbed_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in OFF_SUBSCRIPTION_VARS}
    env.pop("CLAUDECODE", None)  # let a nested `claude -p` start normally
    return env


def build_cmd(model: str, per_call_usd: float, system_message: str | None) -> list[str]:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--setting-sources", "",
           "--strict-mcp-config", "--max-budget-usd", f"{per_call_usd:.4f}"]
    if system_message:
        cmd += ["--system-prompt", system_message]
    assert "--bare" not in cmd  # bare mode = API-key-only auth
    return cmd


class _Lock:
    """Cross-process lock via O_EXCL lock file (works on Windows and POSIX)."""

    def __init__(self, path: Path):
        self.path = path.with_suffix(path.suffix + ".lock")

    def __enter__(self):
        for _ in range(600):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                time.sleep(0.05)
        raise RuntimeError(f"could not acquire {self.path}")

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def ledger_totals(path: Path) -> tuple[float, int]:
    if not path.exists():
        return 0.0, 0
    total, n = 0.0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            total += float(rec.get("cost_usd") or 0.0)
            n += 1
    return total, n


class KairoClaudeCodeLLM(LLMInterface):
    def __init__(self, model_cfg=None):
        self.model = getattr(model_cfg, "name", None) or "haiku"
        self.system_message = getattr(model_cfg, "system_message", None)
        self.timeout = getattr(model_cfg, "timeout", None) or 300
        self.ledger = Path(os.environ["KAIRO_EVOLVE_LEDGER"])
        self.run_budget = float(os.environ["KAIRO_EVOLVE_RUN_BUDGET_USD"])
        self.max_calls = int(os.environ["KAIRO_EVOLVE_MAX_CALLS"])
        self.per_call = float(os.environ["KAIRO_EVOLVE_PER_CALL_USD"])
        self.cwd = os.environ.get("KAIRO_EVOLVE_SANDBOX") or None

    async def generate(self, prompt: str, **kwargs) -> str:
        sys_msg = kwargs.pop("system_message", self.system_message) or ""
        return await self.generate_with_context(
            system_message=sys_msg, messages=[{"role": "user", "content": prompt}], **kwargs)

    async def generate_with_context(self, system_message, messages, **kwargs) -> str:
        user = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._call(system_message, user))

    def _call(self, system_message: str, user: str) -> str:
        with _Lock(self.ledger):
            spent, calls = ledger_totals(self.ledger)
            if spent >= self.run_budget or calls >= self.max_calls:
                raise BudgetExhausted(
                    f"run budget reached: {spent:.4f} USD-equiv / {self.run_budget} cap, "
                    f"{calls} / {self.max_calls} calls")
        t0 = time.time()
        proc = subprocess.run(build_cmd(self.model, self.per_call, system_message),
                              input=user, capture_output=True, text=True, encoding="utf-8",
                              timeout=self.timeout, env=scrubbed_env(), cwd=self.cwd)
        rec = {"t": round(t0, 1), "sec": round(time.time() - t0, 1), "model": self.model,
               "returncode": proc.returncode, "cost_usd": 0.0}
        text = ""
        try:
            out = json.loads(proc.stdout)
            rec["cost_usd"] = float(out.get("total_cost_usd") or 0.0)
            usage = out.get("modelUsage") or {}
            rec["models"] = {k: {"costBasis": v.get("costBasis"), "provider": v.get("provider")}
                             for k, v in usage.items()}
            rec["is_error"] = bool(out.get("is_error"))
            text = out.get("result") or ""
        except (json.JSONDecodeError, TypeError):
            rec["is_error"] = True
            rec["stderr"] = proc.stderr[-400:]
        with _Lock(self.ledger):
            with self.ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        if rec["is_error"] or not text:
            raise RuntimeError(f"claude -p failed (rc={proc.returncode}): "
                               f"{(proc.stderr or proc.stdout)[-300:]}")
        return text


def init_client(model_cfg):
    """OpenEvolve ``init_client`` hook (module-level so spawned workers can unpickle it)."""
    return KairoClaudeCodeLLM(model_cfg)
