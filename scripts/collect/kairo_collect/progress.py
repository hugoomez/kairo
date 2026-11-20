"""progress.json — the contract the Kairo app's "Recolecciones" view displays.

The app starts a collection script as a long detached process with
KAIRO_COLLECTION_ID and KAIRO_COLLECTION_OUT=<dir>, and reads
``<dir>/progress.json`` to show where it is. The file is replaced atomically
(temp file + rename), at most every ``every_s`` seconds (default 2) plus at
every phase change and every rate-limit wait, so a reader never sees half a
file. During a long wait it is rewritten every ~15 s (``updated_at`` moves),
so a stale ``updated_at`` means the process is gone.

    {
      "collection_id": "<KAIRO_COLLECTION_ID or null>",
      "phase": "starting" | "planning" | "collecting" | "replaying" | "done"
               | "interrupted" | "error",
      "done": <int>,                 units finished in this phase
      "total": <int or null>,        units this phase expects (null: unknown)
      "unit": "queries" | "pages" | "items" | ...,
      "waiting_rate_limit_until": "<UTC ISO 8601 Z>" or null,
                                     set while the client sleeps for a GitHub
                                     rate limit (primary reset, secondary
                                     limit / Retry-After, or the per-bucket
                                     minimum interval when it exceeds 10 s)
      "eta_s": <int or null>,        seconds left at the phase's observed pace
                                     (never less than the current wait)
      "updated_at": "<UTC ISO 8601 Z>",
      "message": "<short Spanish text>"   optional: why it stopped, the error
    }

Field names and phase values are stable; new optional fields may be added.
"""
from __future__ import annotations

import time
from pathlib import Path

from .archive import atomic_write_json, utc_iso


class Progress:
    def __init__(self, out: Path, collection_id: str | None = None, clock=time.time, every_s: float = 2.0):
        self.path = Path(out) / "progress.json"
        self.collection_id = collection_id
        self.clock = clock
        self.every_s = every_s
        self.phase = "starting"
        self.done = 0
        self.total: int | None = None
        self.unit = "queries"
        self.waiting_until: float | None = None
        self.message: str | None = None
        self._t0 = clock()
        self._done0 = 0
        self._last_write: float | None = None

    def update(self, phase: str | None = None, done: int | None = None, total: int | None = None,
               unit: str | None = None, message: str | None = None, force: bool = False) -> None:
        if phase is not None and phase != self.phase:
            self.phase, self._t0, force = phase, self.clock(), True
            self._done0 = done if done is not None else 0
            self.message = None
        if done is not None:
            self.done = done
        if total is not None or phase is not None:
            self.total = total
        if unit is not None:
            self.unit = unit
        if message is not None:
            self.message = message
        self._maybe_write(force)

    def waiting(self, until: float | None, reason: str | None = None) -> None:
        self.waiting_until = until
        self._maybe_write(True)

    def eta(self) -> int | None:
        now = self.clock()
        wait = max(0.0, self.waiting_until - now) if self.waiting_until else 0.0
        if self.total is None or self.done <= self._done0:
            return int(wait) if wait else None
        rate = (self.done - self._done0) / max(now - self._t0, 1e-9)
        left = max(0, self.total - self.done) / rate if rate > 0 else None
        return None if left is None else int(max(left, wait))

    def snapshot(self) -> dict:
        snap = {"collection_id": self.collection_id, "phase": self.phase, "done": self.done,
                "total": self.total, "unit": self.unit,
                "waiting_rate_limit_until": utc_iso(self.waiting_until) if self.waiting_until else None,
                "eta_s": self.eta(), "updated_at": utc_iso(self.clock())}
        if self.message:
            snap["message"] = self.message
        return snap

    def _maybe_write(self, force: bool) -> None:
        now = self.clock()
        if not force and self._last_write is not None and now - self._last_write < self.every_s:
            return
        try:
            atomic_write_json(self.path, self.snapshot())
            self._last_write = now
        except OSError:
            pass                    # progress is best effort; the collection goes on
