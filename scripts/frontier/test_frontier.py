"""frontier.py on a tiny invented vault: validation, the «nuestro» layer,
staleness, the cell context, saving an extraction, and deterministic output.
Invented ids and invented text only; everything is written in a temp dir."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frontier as fr  # noqa: E402

PROJ = "PROJ-900"
SLUG = "toy-frontier"


def paper(pid: str, title: str, texto: str, projects: str = f"[{PROJ}]") -> str:
    return f"""---
id: {pid}
title: {title}
year: 2031
projects: {projects}
fulltext: full
---

## Resumen

> Fuente: invented

An invented abstract about toy problems.

## Texto completo

> Fuente: invented

{texto}
"""


P1 = paper("P-9001", "Per-instance toy annealing", """### 1 Introduction

Toy graphs are an invented benchmark for this test only.

### 2 Method

We solve each toy graph instance separately with an invented annealing schedule.

### 3 Results

The invented solver reaches an approximation ratio of 0.93 on forty toy graphs.

### 4 Limitations

Our invented schedule was only tried on graphs with fewer than fifty nodes.
""")

P2 = paper("P-9002", "One network for all toy images", """### 2 Method

A single invented network is trained once and then applied to every toy image.

### 3 Results

The invented network labels toy images in 12 ms each on one invented device.
""")

P3 = paper("P-9003", "A third invented toy study", """### 2 Method

An invented sampler draws toy sentences from a made-up grammar.
""")

EJES = {
    "dominio": {"nombre": "Dominio", "regla": "sobre qué datos trabaja el método", "multivalor": True,
                "valores": {"grafo": "grafos de juguete", "imagen": "imágenes de juguete", "texto": "frases de juguete"}},
    "metodo": {"nombre": "Método", "regla": "cómo se resuelve cada instancia", "multivalor": True,
               "valores": {"por_instancia": "se optimiza cada instancia", "amortizado": "un modelo para todas"}},
    "escala": {"nombre": "Escala", "regla": "tamaño máximo probado", "multivalor": False,
               "valores": {"pequena": "menos de 100 nodos", "grande": "100 o más"}},
}

MAPA = {
    "meta": {"proyecto": PROJ, "fecha": "2031-01-10", "escrito_por": "modelo", "citable": False,
             "aviso_cobertura": "Corpus inventado de tres papers; la búsqueda no cubrió texto.",
             "apuesta": "un modelo de juguete amortizado"},
    "ejes": EJES,
    "papers": [{"id": "P-9001", "nombre_corto": "ToyAnneal"}],
    "ideas": [{"id": "recocido", "nombre": "Recocido", "descripcion": "un recocido inventado"},
              {"id": "red_unica", "nombre": "Red única", "descripcion": "una red para todo"}],
    "relaciones": [{"de": "red_unica", "tipo": "compite_con", "a": "recocido", "origen": "deducida",
                    "citas": [{"paper": "P-9001", "loc": "§2"}]}],
    "matrices": [{"id": "M1", "titulo": "Dominio × método", "pregunta": "¿Quién ha resuelto qué y cómo?",
                  "filas": {"eje": "dominio", "valores": ["grafo", "imagen", "texto"]},
                  "columnas": {"eje": "metodo", "valores": ["por_instancia", "amortizado"]},
                  "por_defecto": {"fila": {"texto": {"marca": "sin_sentido", "por_que": "fuera del alcance inventado"}}},
                  "vacias": {"grafo|amortizado": {"marca": "hueco", "por_que": "nadie entrena un modelo para grafos"},
                             "imagen|por_instancia": {"marca": "no_buscado", "por_que": "la búsqueda no lo miró"}}}],
    "frontera": [{"id": "F1", "aspecto": "Calidad en grafos", "refs": ["P-9001#r1"], "mejor": "P-9001#r1",
                  "comparables": True, "nota": "Un solo resultado inventado."}],
    "huecos": [{"id": "G1", "prioridad": 1, "titulo": "Modelo amortizado para grafos de juguete",
                "por_que": "Ningún paper del corpus inventado lo hace.",
                "evidencia": [{"ref": "P-9001", "loc": "§2", "texto": "resuelve cada instancia por separado"}],
                "celdas": ["M1:grafo|amortizado"], "realidad": "real en el corpus", "dificultad": "media",
                "relevancia": "alta", "barato": {"si": True, "por_que": "CPU, minutos"},
                "encaje": {"nivel": "directo", "texto": "es la apuesta"}, "tarea": "H-9001"}],
    "posicionamiento": {"nuevo": ["amortizar en grafos de juguete"], "ya_existe": ["recocido por instancia"],
                        "no_podemos_afirmar": ["nada sobre texto"]},
}


def extraccion(pid: str, ejes: dict, resultados=None, limitaciones=None, ideas=None, sha: str | None = None) -> dict:
    return {"paper": pid, "taxonomia_sha": sha or fr.sha_taxonomia(EJES), "extraido": "2031-01-09",
            "ejes": ejes, "resultados": resultados or [], "limitaciones": limitaciones or [],
            "ideas": ideas or [], "no_leido": []}


EX1 = extraccion(
    "P-9001",
    {"dominio": {"valores": ["grafo"], "citas": [{"cita": "We solve each toy graph instance separately", "loc": "§2"}]},
     "metodo": {"valores": ["por_instancia"],
                "citas": [{"cita": "We solve each toy graph instance separately with an invented annealing schedule.",
                           "loc": "§2"}]},
     "escala": {"valores": ["pequena"],
                "citas": [{"cita": "only tried on graphs with fewer than fifty nodes", "loc": "§4"}]}},
    resultados=[{"id": "r1", "aspecto": "calidad", "valor": "razón de aproximación 0.93",
                 "condiciones": {"grafos": "forty toy graphs"},
                 "cita": "The invented solver reaches an approximation ratio of 0.93 on forty toy graphs.",
                 "loc": "§3"}],
    limitaciones=[{"origen": "autores", "texto": "solo grafos pequeños",
                   "cita": "Our invented schedule was only tried on graphs with fewer than fifty nodes.", "loc": "§4"}],
    ideas=["recocido"])

EX2 = extraccion(
    "P-9002",
    {"dominio": {"valores": ["imagen"], "citas": [{"cita": "applied to every toy image", "loc": "§2"}]},
     "metodo": {"valores": ["amortizado"],
                "citas": [{"cita": "A single invented network is trained once", "loc": "§2"}]},
     "escala": {"valores": [], "citas": [], "nota": "No está en el texto"}},
    resultados=[{"id": "r1", "aspecto": "latencia", "valor": "12 ms por imagen",
                 "cita": "The invented network labels toy images in 12 ms each on one invented device.", "loc": "§3"}],
    ideas=["red_unica"])

HUB = f"""---
id: {PROJ}
name: Proyecto de juguete
type: ciencia
code_repo: <path>
---

# Proyecto de juguete
"""

H1 = """---
id: H-9001
project: PROJ-900
status: propuesta
updated: 2031-01-12
frontier_cells: [M1:grafo|amortizado]
---

## Claim

Un modelo de juguete amortizado iguala al recocido inventado.
"""

H2 = """---
id: H-9002
project: PROJ-900
status: en_cola
frontier_cells:
  - M1:imagen|por_instancia
  - M1:no|existe
---

## Claim

Otra afirmación inventada.
"""

E1 = """---
id: E-9001
hypothesis: H-9001
project: PROJ-900
status: completed
result:
  metric_value: 0.5
  verdict: inconclusa
---
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kairo-frontier-"))
        (self.vault / "Papers").mkdir()
        for pid, text in (("P-9001", P1), ("P-9002", P2), ("P-9003", P3)):
            (self.vault / "Papers" / f"{pid} toy.md").write_text(text, encoding="utf-8")
        self.pdir = self.vault / "Projects" / SLUG
        for sub in ("Hipotesis", "Experimentos", "Frontera/extracciones"):
            (self.pdir / sub).mkdir(parents=True)
        (self.pdir / "_hub.md").write_text(HUB, encoding="utf-8")
        (self.pdir / "Hipotesis" / "H-9001.md").write_text(H1, encoding="utf-8")
        (self.pdir / "Hipotesis" / "H-9002.md").write_text(H2, encoding="utf-8")
        (self.pdir / "Experimentos" / "E-9001.md").write_text(E1, encoding="utf-8")
        self.front = self.pdir / "Frontera"
        self.write_map(MAPA)
        self.write_ex(EX1)
        self.write_ex(EX2)
        fr._CACHE_CITAS.clear()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def write_map(self, d: dict):
        (self.front / "mapa.yaml").write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def write_ex(self, ex: dict):
        (self.front / "extracciones" / f"{ex['paper']}.json").write_text(json.dumps(ex, ensure_ascii=False),
                                                                         encoding="utf-8")

    def run_cli(self, *args) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fr.main([args[0], *args[1:], "--vault", str(self.vault), "--project", PROJ])
        return code, json.loads(buf.getvalue())

    def mapa(self, d: dict | None = None):
        m = copy.deepcopy(d or MAPA)
        self.write_map(m)
        return m


class Valid(Base):
    def test_check_passes_and_counts_quotes(self):
        code, rep = self.run_cli("check")
        self.assertEqual(code, 0, rep["errores"])
        self.assertEqual(rep["citas"]["total"], rep["citas"]["ok"])
        self.assertGreaterEqual(rep["citas"]["total"], 7)
        self.assertTrue(any("H-9002" in a and "M1:no|existe" in a for a in rep["avisos"]))

    def test_project_by_slug_too(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fr.main(["check", "--vault", str(self.vault), "--project", SLUG])
        self.assertEqual(code, 0)

    def test_build_writes_views_and_the_nuestro_layer(self):
        code, rep = self.run_cli("build")
        self.assertEqual(code, 0, rep.get("errores"))
        for name in ("matriz.md", "frontera.md", "huecos.md", "posicionamiento.md", "mapa.json", "mapa.html"):
            self.assertTrue((self.front / name).is_file(), name)
        d = json.loads((self.front / "mapa.json").read_text(encoding="utf-8"))
        self.assertEqual(d["proyecto"], PROJ)
        m = d["matrices"][0]
        cell = {c["id"]: c for row in m["celdas"] for c in row}
        ga = cell["M1:grafo|amortizado"]
        self.assertEqual(ga["papers"], [])
        self.assertEqual(ga["marca"], "hueco")
        self.assertEqual(ga["nuestros"][0], {"id": "H-9001", "tipo": "hipotesis", "status": "propuesta"})
        self.assertEqual(ga["nuestros"][1]["id"], "E-9001")
        self.assertEqual(ga["nuestros"][1]["veredicto"], "inconclusa")
        self.assertEqual(cell["M1:grafo|por_instancia"]["papers"], ["P-9001"])
        self.assertIsNone(cell["M1:grafo|por_instancia"]["marca"])
        self.assertEqual(cell["M1:texto|amortizado"]["marca"], "sin_sentido")
        self.assertEqual(cell["M1:imagen|por_instancia"]["nuestros"][0]["id"], "H-9002")
        self.assertEqual(d["huecos"][0]["atacado_por"], [{"id": "H-9001", "status": "propuesta"}])
        self.assertEqual(d["obsoleto"]["papers_sin_extraer"], ["P-9003"])
        self.assertEqual(d["resumen"]["casillas"], {"cubiertas": 2, "hueco": 1, "no_buscado": 1, "sin_sentido": 2})
        self.assertEqual(d["generado"], "2031-01-12")      # latest input date, not the clock
        self.assertIn('id="datos"', (self.front / "mapa.html").read_text(encoding="utf-8"))
        self.assertIn("H-9001", (self.front / "huecos.md").read_text(encoding="utf-8"))

    def test_build_is_deterministic(self):
        self.assertEqual(self.run_cli("build")[0], 0)
        first = {p.name: p.read_bytes() for p in self.front.iterdir() if p.is_file()}
        fr._CACHE_CITAS.clear()
        self.assertEqual(self.run_cli("build")[0], 0)
        second = {p.name: p.read_bytes() for p in self.front.iterdir() if p.is_file()}
        self.assertEqual(first, second)

    def test_manual_component_is_ours_and_not_coverage(self):
        d = copy.deepcopy(MAPA)
        d["nuestro_proyecto"] = {"componentes": [{"id": "baseline", "nombre": "Línea base inventada", "estado": "hecho",
                                                  "ejes": {"dominio": ["grafo"], "metodo": ["amortizado"]}}]}
        self.write_map(d)
        code, rep = self.run_cli("cell", "M1:grafo|amortizado")
        self.assertEqual(code, 0)
        self.assertEqual(rep["papers"], [])
        self.assertIn({"id": "baseline", "tipo": "componente", "status": "hecho", "nombre": "Línea base inventada"},
                      rep["nuestros"])

    def test_repo_citation_skipped_without_code_repo(self):
        d = copy.deepcopy(MAPA)
        d["nuestro_proyecto"] = {"componentes": [{"id": "c1", "nombre": "x", "estado": "hecho", "resultados": [
            {"id": "r1", "aspecto": "calidad", "valor": "0.5", "fuente_repo": "docs/x.md", "cita": "vale 0.5"}]}]}
        self.write_map(d)
        code, rep = self.run_cli("check")
        self.assertEqual(code, 0, rep["errores"])
        self.assertTrue(any("fuente_repo" in a and "code_repo" in a for a in rep["avisos"]))


class Invalid(Base):
    def assert_error(self, d: dict, needle: str):
        self.write_map(d)
        code, rep = self.run_cli("build")
        self.assertEqual(code, 1)
        self.assertTrue(any(needle in e for e in rep["errores"]), rep["errores"])
        self.assertFalse((self.front / "mapa.json").exists())
        self.assertFalse((self.front / "matriz.md").exists())

    def test_undefined_axis_value(self):
        d = copy.deepcopy(MAPA)
        d["papers"] = [{"id": "P-9001", "ejes": {"dominio": ["galaxia"]}}]
        self.assert_error(d, "valor «galaxia» no definido en el eje «dominio»")

    def test_undefined_value_in_extraction(self):
        ex = copy.deepcopy(EX2)
        ex["ejes"]["metodo"]["valores"] = ["magia"]
        self.write_ex(ex)
        self.assert_error(MAPA, "valor «magia» no definido")

    def test_scalar_axis_given_a_list(self):
        d = copy.deepcopy(MAPA)
        d["papers"] = [{"id": "P-9001", "ejes": {"escala": ["pequena", "grande"]}}]
        self.assert_error(d, "admite un solo valor")

    def test_number_not_in_quote(self):
        ex = copy.deepcopy(EX1)
        ex["resultados"][0]["valor"] = "razón de aproximación 0.97"
        self.write_ex(ex)
        self.assert_error(MAPA, "cifras del valor que no están en la cita: 0.97")

    def test_quote_not_in_paper(self):
        ex = copy.deepcopy(EX2)
        ex["ejes"]["metodo"]["citas"] = [{"cita": "A single invented network is retrained for every image", "loc": "§2"}]
        self.write_ex(ex)
        self.assert_error(MAPA, "la cita no es texto del paper")

    def test_quote_under_the_wrong_locator(self):
        ex = copy.deepcopy(EX1)
        ex["resultados"][0]["loc"] = "§2"
        self.write_ex(ex)
        self.assert_error(MAPA, "P-9001#r1")

    def test_unmarked_empty_cell(self):
        d = copy.deepcopy(MAPA)
        del d["matrices"][0]["vacias"]["imagen|por_instancia"]
        self.assert_error(d, "M1:imagen|por_instancia: casilla vacía sin marca")

    def test_mark_without_reason(self):
        d = copy.deepcopy(MAPA)
        d["matrices"][0]["vacias"]["grafo|amortizado"] = {"marca": "hueco", "por_que": " "}
        self.assert_error(d, "M1:grafo|amortizado: marca no válida o sin razón")

    def test_gap_without_cheapness_and_fit(self):
        d = copy.deepcopy(MAPA)
        del d["huecos"][0]["barato"]
        d["huecos"][0]["encaje"] = {"nivel": "cercano", "texto": "x"}
        self.write_map(d)
        code, rep = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("falta `barato`" in e for e in rep["errores"]))
        self.assertTrue(any("encaje.nivel" in e for e in rep["errores"]))

    def test_gap_on_unknown_cell(self):
        d = copy.deepcopy(MAPA)
        d["huecos"][0]["celdas"] = ["M1:grafo|volador"]
        self.assert_error(d, "casilla «M1:grafo|volador» no existe")


class Stale(Base):
    def test_unextracted_old_taxonomy_and_covered_marks(self):
        old = copy.deepcopy(EX2)
        old["taxonomia_sha"] = "0" * 64
        self.write_ex(old)
        d = copy.deepcopy(MAPA)
        d["matrices"][0]["vacias"]["grafo|por_instancia"] = {"marca": "hueco", "por_que": "marcada antes de P-9001"}
        # P-9002 is out (old taxonomy): its cell needs a mark for the map to stay valid
        d["matrices"][0]["vacias"]["imagen|amortizado"] = {"marca": "no_buscado", "por_que": "pendiente"}
        self.write_map(d)
        code, rep = self.run_cli("stale")
        self.assertEqual(code, 0)
        self.assertEqual(rep["papers_sin_extraer"], ["P-9003"])
        self.assertEqual(rep["taxonomia_cambiada"], ["P-9002"])
        self.assertEqual(rep["marcas_cubiertas"],
                         [{"celda": "M1:grafo|por_instancia", "marca": "hueco", "papers": ["P-9001"]}])
        self.assertFalse(rep["al_dia"])
        self.assertIn("P-9003", rep["rutas"])
        code, rep = self.run_cli("check")
        self.assertEqual(code, 0, rep["errores"])
        self.assertTrue(any("P-9002" in a and "otra taxonomía" in a for a in rep["avisos"]))

    def test_changing_the_taxonomy_makes_every_extraction_stale(self):
        d = copy.deepcopy(MAPA)
        d["ejes"]["escala"]["valores"]["enorme"] = "más de mil"
        self.write_map(d)
        code, rep = self.run_cli("stale")
        self.assertEqual(sorted(rep["taxonomia_cambiada"]), ["P-9001", "P-9002"])

    def test_gap_covered_by_a_new_paper(self):
        ex = copy.deepcopy(EX2)
        ex["ejes"]["dominio"]["valores"] = ["imagen", "grafo"]
        self.write_ex(ex)
        code, rep = self.run_cli("stale")
        self.assertIn({"hueco": "G1", "celda": "M1:grafo|amortizado", "papers": ["P-9002"]}, rep["huecos_cubiertos"])
        self.assertIn("M1:grafo|amortizado", [m["celda"] for m in rep["marcas_cubiertas"]])

    def test_no_map_yet(self):
        (self.front / "mapa.yaml").unlink()
        code, rep = self.run_cli("stale")
        self.assertTrue(rep["sin_mapa"])
        self.assertEqual(rep["papers_sin_extraer"], ["P-9001", "P-9002", "P-9003"])


class Cell(Base):
    def test_covered_cell(self):
        code, rep = self.run_cli("cell", "M1:grafo|por_instancia")
        self.assertEqual(code, 0)
        self.assertEqual(rep["fila"]["valor"], "grafo")
        self.assertEqual(rep["fila"]["desc"], "grafos de juguete")
        self.assertEqual(rep["columna"]["eje_nombre"], "Método")
        self.assertEqual([p["id"] for p in rep["papers"]], ["P-9001"])
        self.assertEqual(rep["papers"][0]["titulo"], "Per-instance toy annealing")
        self.assertEqual(rep["papers"][0]["confianza"], "leido_completo")
        self.assertNotIn("cita", rep["papers"][0]["evidencia"][0])
        self.assertIsNone(rep["marca"])

    def test_gap_cell_with_our_hypothesis(self):
        code, rep = self.run_cli("cell", "M1:grafo|amortizado", "--con-citas")
        self.assertEqual(rep["marca"], "hueco")
        self.assertEqual(rep["por_que"], "nadie entrena un modelo para grafos")
        self.assertEqual(rep["nuestros"][0]["id"], "H-9001")
        self.assertTrue(rep["nuestros"][0]["titulo"].startswith("Un modelo de juguete"))
        self.assertEqual(rep["huecos"][0]["id"], "G1")
        self.assertEqual(rep["huecos"][0]["atacado_por"][0]["id"], "H-9001")

    def test_quotes_on_request(self):
        code, rep = self.run_cli("cell", "M1:grafo|por_instancia", "--con-citas")
        self.assertIn("cita", rep["papers"][0]["evidencia"][0])

    def test_unknown_cell(self):
        code, rep = self.run_cli("cell", "M1:grafo|volador")
        self.assertEqual(code, 2)
        self.assertIn("error", rep)


class SaveExtraction(Base):
    def test_keeps_what_the_text_supports(self):
        (self.front / "extracciones" / "P-9003.json").unlink(missing_ok=True)
        reply = self.vault / "_respuesta-extractor.md"
        block = {"extracciones": [{
            "paper": "P-9003",
            "ejes": {"dominio": {"valores": ["texto"], "citas": [{"cita": "draws toy sentences from a made-up grammar",
                                                                  "loc": "§2"}]},
                     "metodo": {"valores": ["por_instancia"],
                                "citas": [{"cita": "trains a separate sampler per sentence", "loc": "§2"}]},
                     "escala": {"valores": ["volcanica"], "citas": []}},
            "resultados": [{"aspecto": "x", "valor": "0.99", "cita": "An invented sampler draws toy sentences",
                            "loc": "§2"}],
            "ideas": ["recocido", "inventada"]},
            {"paper": "P-0001", "ejes": {}}],
            "suspicious": []}
        reply.write_text("Aquí va:\n\n```json\n" + json.dumps(block) + "\n```\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fr.main(["save-extraction", "--vault", str(self.vault), "--project", PROJ, "--reply", str(reply),
                            "--date", "2031-02-01"])
        rep = json.loads(buf.getvalue())
        self.assertEqual(code, 0, rep)
        saved = json.loads((self.front / "extracciones" / "P-9003.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["taxonomia_sha"], fr.sha_taxonomia(EJES))
        self.assertEqual(saved["extraido"], "2031-02-01")
        self.assertEqual(saved["ejes"]["dominio"]["valores"], ["texto"])
        self.assertEqual(saved["ejes"]["metodo"]["valores"], [])          # its only quote failed
        self.assertEqual(saved["ejes"]["escala"]["valores"], [])          # undefined value
        self.assertEqual(saved["resultados"], [])                          # number not in quote
        self.assertEqual(saved["ideas"], ["recocido"])
        motivos = " ".join(x["motivo"] for x in saved["descartado"])
        self.assertIn("volcanica", motivos)
        self.assertIn("inventada", motivos)
        self.assertEqual(rep["rechazados"][0]["paper"], "P-0001")

    def test_taxonomy_lists_axes_and_sha(self):
        code, rep = self.run_cli("taxonomy")
        self.assertEqual(rep["taxonomia_sha"], fr.sha_taxonomia(EJES))
        self.assertEqual(set(rep["ejes"]), {"dominio", "metodo", "escala"})


if __name__ == "__main__":
    unittest.main()
