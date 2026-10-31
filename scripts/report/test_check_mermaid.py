"""Tests for check_mermaid.py. Run: python -m pytest scripts/report

Invented diagrams only."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_mermaid as cm  # noqa: E402

GOOD = """# Nota

Texto antes.

```mermaid
%% un comentario
flowchart TD
  A["Entrada: grafo de 5 vértices"] --> B{¿Hay conflicto?}
  B -->|sí| C[(Memoria)]
  B -->|no| D(["Salida (conjunto)"])
  E>Bandera] --> D
```

```mermaid
mindmap
  root((Tema central))
    Idea uno
    Idea dos
```

```mermaid
sequenceDiagram
  A->>B: paso 1) sin paréntesis de apertura
  B-->>A: respuesta
```

```python
print("[sin cerrar")
```
"""


def problems(text: str) -> list[str]:
    return [p["reason"] for p in cm.lint(text)["problems"]]


def block(body: str) -> str:
    return f"Antes.\n\n```mermaid\n{body}\n```\n"


class TestCheckMermaid(unittest.TestCase):
    def test_good_diagrams_pass_and_other_code_is_ignored(self):
        r = cm.lint(GOOD)
        self.assertEqual((r["blocks"], r["ok"], r["problems"]), (3, 3, []))

    def test_unknown_type(self):
        self.assertIn("no es un tipo de diagrama conocido", problems(block("flowchat TD\n  A --> B"))[0])

    def test_unknown_direction(self):
        self.assertIn("dirección", problems(block("flowchart XY\n  A --> B"))[0])

    def test_empty_and_type_only(self):
        self.assertEqual(problems(block("")), ["diagrama vacío"])
        self.assertEqual(problems(block("%% solo comentario")), ["diagrama vacío"])
        self.assertIn("solo tiene la línea del tipo", problems(block("flowchart LR"))[0])

    def test_unclosed_fence(self):
        self.assertIn("no se cierra", problems("```mermaid\nflowchart TD\n  A --> B\n")[0])

    def test_unbalanced_brackets(self):
        self.assertIn("sin cerrar", problems(block("flowchart TD\n  A[Paso uno --> B"))[0])
        self.assertIn("sin abrir", problems(block("flowchart TD\n  A --> B]"))[0])
        self.assertIn("sin cerrar", problems(block("mindmap\n  root((Tema)"))[0])

    def test_odd_quotes(self):
        self.assertEqual(problems(block('flowchart TD\n  A["Sin cerrar] --> B')), ["comillas sin cerrar"])

    def test_unquoted_parentheses_in_a_label(self):
        self.assertIn("paréntesis", problems(block("flowchart TD\n  A[f(x) crece] --> B"))[0])
        self.assertEqual(problems(block('flowchart TD\n  A["f(x) crece"] --> B')), [])

    def test_config_header_is_skipped(self):
        self.assertEqual(problems(block("---\ntitle: Prueba\n---\nflowchart LR\n  A --> B")), [])

    def test_line_numbers_point_into_the_note(self):
        r = cm.lint(block("flowchart TD\n  A --> B\n  C[sin cerrar --> D"))
        self.assertEqual(r["problems"][0]["line"], 6)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.md"), Path(d, "bad.md")
            good.write_text(GOOD, encoding="utf-8")
            bad.write_text(block("flowchart TD\n  A[roto --> B"), encoding="utf-8")
            for path, code in ((good, 0), (bad, 2), (Path(d, "missing.md"), 1)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cm.main([str(path)]), code)
                self.assertIn("tool", json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
