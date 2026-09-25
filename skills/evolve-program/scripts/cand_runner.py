"""Runs one evolved candidate in its own process (started by harness.py).

Reads {"inputs": [...]} on stdin, imports ./cand.py, calls its entry point once
per input, prints one line ``@@KAIRO_OUTPUT@@<json list>``. It never sees
targets, scores, the lock, or the held-out split. Anything the candidate
prints itself is ignored except that final marker line — and a candidate that
prints its own marker line still only controls its *outputs*, which the harness
validates and scores.
"""

import importlib.util
import json
import sys

MARKER = "@@KAIRO_OUTPUT@@"


def main() -> int:
    entry = sys.argv[1]
    payload = json.loads(sys.stdin.read())
    spec = importlib.util.spec_from_file_location("cand", "cand.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, entry)
    outputs = [fn(x) for x in payload["inputs"]]
    sys.stdout.write("\n" + MARKER + json.dumps(outputs, allow_nan=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
