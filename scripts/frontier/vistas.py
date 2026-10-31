"""Views of the frontier map: the Markdown pages and the interactive mapa.html.

Used by frontier.py `build`. Every view is a pure function of the map, so the
same inputs give the same bytes. mapa.html embeds mapa.json and its script only
draws it; no external dependency beyond Google Fonts (with system fallbacks).
"""

from __future__ import annotations

import html
import json

AVISO = "<!-- Generado por scripts/frontier/frontier.py a partir de mapa.yaml. No editar a mano. -->\n\n"


def md(texto) -> str:
    return str(texto if texto is not None else "").replace("|", "\\|").replace("\n", " ")


def _aviso_cobertura(mapa) -> list[str]:
    aviso = str((mapa.d.get("meta") or {}).get("aviso_cobertura") or "").strip()
    return [f"> {aviso}\n"] if aviso else []


def _apuesta(mapa) -> str:
    return str((mapa.d.get("meta") or {}).get("apuesta") or "la apuesta del proyecto").strip()


def _nuestro_txt(n: dict) -> str:
    return f"{n['id']} ({n['tipo']}, {n['status']})"


def vista_matriz(mapa) -> str:
    out = [AVISO, "# Matriz de cobertura\n",
           "Una casilla está cubierta si algún paper del corpus tiene el valor de la fila **y** el de la columna. "
           "Las casillas vacías llevan una marca: `hueco` (tiene sentido y nadie lo ha hecho, según el corpus), "
           "`sin_sentido` (no aplica) o `no_buscado` (el corpus no permite saberlo). "
           "Lo nuestro (hipótesis, experimentos y componentes) aparece en cursiva y no cuenta como cobertura.\n",
           *_aviso_cobertura(mapa),
           "Aviso de método: un paper con varios valores en un eje cubre todas sus combinaciones, "
           "aunque no las haya probado juntas. Las asignaciones y su evidencia están en `extracciones/` y `mapa.yaml`.\n"]
    for m in mapa.matrices:
        celdas = mapa.calcular_matriz(m)
        fe, ce = m["filas"]["eje"], m["columnas"]["eje"]
        cols = m["columnas"]["valores"]
        out.append(f"\n## {m['id']}. {m.get('titulo', '')}\n")
        if m.get("pregunta"):
            out.append(f"{m['pregunta']}\n")
        filtro = mapa._filtro(m)
        if filtro:
            out.append(f"Solo papers con `{filtro[0]}` en {', '.join('`' + v + '`' for v in sorted(filtro[1]))}.\n")
        out.append(f"| {md(mapa.nombre_eje(fe))} \\ {md(mapa.nombre_eje(ce))} | "
                   + " | ".join(f"`{c}`" for c in cols) + " |")
        out.append("|---" * (len(cols) + 1) + "|")
        notas = []
        for fila in m["filas"]["valores"]:
            fila_txt = [f"`{fila}`"]
            for col in cols:
                e = celdas[(fila, col)]
                partes = []
                if e["papers"]:
                    partes.append(", ".join(mapa.nombre(p) for p in e["papers"]))
                else:
                    notas.append((fila, col, e["marca"]))
                    partes.append(f"**{e['marca']['marca']}** [{len(notas)}]")
                if e["nuestros"]:
                    partes.append("_" + ", ".join(_nuestro_txt(n) for n in e["nuestros"]) + "_")
                fila_txt.append("<br>".join(md(x) for x in partes))
            out.append("| " + " | ".join(fila_txt) + " |")
        if notas:
            out.append("\nCasillas vacías:\n")
            for i, (fila, col, marca) in enumerate(notas, 1):
                out.append(f"{i}. `{m['id']}:{fila}|{col}` — **{marca['marca']}**: {md(marca['por_que'])}")
    return "\n".join(out) + "\n"


def _ref_fuente(mapa, ref: str) -> str:
    quien, r = mapa.resultados[ref]
    if quien.startswith("NUESTRO:"):
        return f"nuestro ({r['fuente_repo']})" if r.get("fuente_repo") else "nuestro (brief)"
    fuente = r.get("fuente", quien)
    extra = f", vía {fuente}" if fuente != quien else ""
    return f"{quien} {r.get('loc', '')}{extra}".strip()


def _confianza(mapa, ref: str) -> str:
    quien, r = mapa.resultados[ref]
    if quien.startswith("NUESTRO:"):
        return "repo" if r.get("fuente_repo") else "brief"
    return mapa._confianza(quien)


def vista_frontera(mapa) -> str:
    out = [AVISO, "# Frontera: mejor resultado conocido por aspecto\n",
           "Cada cifra está en la cita literal del paper que guarda la extracción, y la cita se ha comprobado "
           "contra el texto del paper (`frontier.py check`). ★ marca el mejor resultado cuando la comparación "
           "tiene sentido; si no lo tiene, se dice.\n"]
    for f in mapa.d.get("frontera") or []:
        out.append(f"\n## {f.get('aspecto', f.get('id'))}\n")
        out.append("| | Resultado | Condiciones | Fuente | Confianza |")
        out.append("|---|---|---|---|---|")
        for ref in f.get("refs") or []:
            _, r = mapa.resultados[ref]
            c = r.get("condiciones") or {}
            cond = "; ".join(f"{k}: {v}" for k, v in c.items() if v not in (None, "", "—"))
            estrella = "★" if f.get("mejor") == ref else ""
            out.append(f"| {estrella} | {md(r.get('valor'))} | {md(cond)} | {md(_ref_fuente(mapa, ref))} | "
                       f"{_confianza(mapa, ref)} |")
        if f.get("mejor"):
            out.append(f"\n**Mejor conocido:** {md(mapa.resultados[f['mejor']][1].get('valor'))} "
                       f"({_ref_fuente(mapa, f['mejor'])}).")
        else:
            out.append("\n**Mejor conocido:** no se puede elegir uno.")
        out.append(f"**¿Comparables?** {'sí' if f.get('comparables') else 'no'}. {str(f.get('nota') or '').strip()}")
        for ref in f.get("refs") or []:
            nota = mapa.resultados[ref][1].get("nota")
            if nota:
                out.append(f"\n_Nota sobre {ref}:_ {nota}")
    return "\n".join(out) + "\n"


def vista_huecos(mapa) -> str:
    apuesta = _apuesta(mapa)
    out = [AVISO, "# Huecos priorizados\n",
           "Ordenados por prioridad para nosotros. «Real» quiere decir que el hueco está en el corpus; "
           "si el corpus no se buscó para encontrarlo, se dice. Las casillas citadas se recalculan en cada "
           "ejecución: si un paper nuevo las cubre, aparece un aviso.\n",
           *_aviso_cobertura(mapa),
           f"★ = se puede comprobar con poco cómputo. «Encaje» dice cómo se relaciona cada hueco con {apuesta}.\n",
           "| # | Hueco | ★ | Encaje | Lo atacan |", "|---|---|---|---|---|"]
    huecos = sorted(mapa.d.get("huecos") or [], key=lambda h: (h.get("prioridad", 99), str(h.get("id"))))
    for i, h in enumerate(huecos, 1):
        estrella = "★" if (h.get("barato") or {}).get("si") else ""
        atacan = ", ".join(f"{a['id']} ({a['status']})" for a in mapa.atacado_por(h.get("celdas") or [])) or "—"
        out.append(f"| {i} | {md(h.get('titulo'))} | {estrella} | {(h.get('encaje') or {}).get('nivel', '')} | {md(atacan)} |")
    out.append("\nNiveles de encaje: " + "; ".join(f"**{k}** = {v}" for k, v in mapa.niveles.items()) + ".\n")
    for i, h in enumerate(huecos, 1):
        barato = h.get("barato") or {}
        encaje = h.get("encaje") or {}
        out.append(f"\n## {i}. {'★ ' if barato.get('si') else ''}{h.get('titulo')}\n")
        out.append(f"**Coste de comprobarlo:** {'★ poco cómputo' if barato.get('si') else 'no es barato'}. "
                   f"{barato.get('por_que', '')}\n")
        out.append(f"**Encaje:** {encaje.get('nivel', '')}. {encaje.get('texto', '')}\n")
        out.append(f"**Por qué es un hueco.** {str(h.get('por_que') or '').strip()}\n")
        if h.get("evidencia"):
            out.append("**Evidencia.**\n")
            for ev in h["evidencia"]:
                ref = "Corpus" if ev.get("ref") == "meta" else ev.get("ref", "")
                loc = f" {ev['loc']}" if ev.get("loc") else ""
                out.append(f"- {ref}{loc}: {ev.get('texto', '')}")
        for celda in h.get("celdas") or []:
            e = mapa.estado_celda(celda)
            if e["papers"]:
                out.append(f"- ⚠ Casilla `{celda}`: **ya la cubre {', '.join(e['papers'])}**; revisar este hueco.")
            else:
                out.append(f"- Casilla `{celda}`: vacía, marcada `{e['marca']['marca']}`.")
        atacan = mapa.atacado_por(h.get("celdas") or [])
        out.append("")
        if h.get("realidad"):
            out.append(f"**¿Real?** {h['realidad']}\n")
        out.append(f"**Dificultad:** {h.get('dificultad', '—')}. **Relevancia para nosotros:** {h.get('relevancia', '—')}.\n")
        out.append(f"**Qué tarea nuestra lo ataca:** {h.get('tarea', '')}")
        if atacan:
            out.append("\n**Hipótesis en sus casillas:** " + ", ".join(f"{a['id']} ({a['status']})" for a in atacan))
    return "\n".join(out) + "\n"


def vista_posicionamiento(mapa) -> str:
    pos = mapa.d.get("posicionamiento") or {}
    hip = mapa.d.get("hipotesis_clave") or {}
    out = [AVISO, "# Posicionamiento: dónde caemos nosotros\n", "## Dónde caemos en la matriz\n",
           "Calculado de las notas: hipótesis con `frontier_cells` (y sus experimentos) y los componentes de "
           "`nuestro_proyecto`. «Ya existe» = algún paper cubre la casilla.\n",
           "| Nuestro | Matriz | Casilla | Papers que ya la cubren |", "|---|---|---|---|"]
    for m in mapa.matrices:
        for e in mapa.calcular_matriz(m).values():
            for n in e["nuestros"]:
                ya = ", ".join(mapa.nombre(p) for p in e["papers"]) or f"ninguno (marca: {e['marca']['marca']})"
                out.append(f"| {md(_nuestro_txt(n))} | {m['id']} | `{e['id']}` | {md(ya)} |")
    for titulo, clave in (("Qué es nuevo", "nuevo"), ("Qué ya existe", "ya_existe"),
                          ("Qué no podemos afirmar todavía", "no_podemos_afirmar")):
        out.append(f"\n## {titulo}\n")
        for item in pos.get(clave) or []:
            out.append(f"- {item}")
    if hip:
        out.append("\n## Hipótesis clave\n")
        out.append(f"_{hip.get('enunciado', '')}_\n")
        for titulo, clave in (("A favor", "a_favor"), ("En contra o matizando", "en_contra")):
            out.append(f"**{titulo}:**\n")
            for e in hip.get(clave) or []:
                loc = f" {e['loc']}" if e.get("loc") else ""
                quien = "Corpus" if e.get("ref") == "meta" else e.get("ref", "")
                out.append(f"- {quien}{loc}: {e.get('texto', '')}")
            out.append("")
        out.append(f"**Veredicto:** {str(hip.get('veredicto') or '').strip()}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- mapa.html

CSS = r"""
:root {
  --bg: #f3f5f7; --surface: #ffffff; --ink: #14212b; --muted: #586774; --line: #d7dde3;
  --accent: #0b7380; --accent-ink: #ffffff; --accent-soft: #d5ecee;
  --hueco: #c0392f; --hueco-soft: #f9dedb; --nob: #a66d12; --nob-soft: #f5e7c9;
  --sin: #87929c; --sin-soft: #e9edf0; --nuestro: #d27a22; --ambos: #6552c9;
  --cov1: #d3ebee; --cov2: #a2d4da; --cov3: #4fa8b3; --cov4: #1f7f8c; --cov-ink: #0d2a30;
  --r-base: #5b6b78; --r-compite: #c0392f; --r-combina: #2f8a55; --r-contradice: #6552c9;
  --f-display: "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --f-body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f151a; --surface: #161f26; --ink: #e3e9ee; --muted: #98a6b2; --line: #28343e;
    --accent: #48bfcb; --accent-ink: #062126; --accent-soft: #12333a;
    --hueco: #f07a6f; --hueco-soft: #3b1d1b; --nob: #e3ad4f; --nob-soft: #372a12;
    --sin: #6c7984; --sin-soft: #1d262e; --nuestro: #f0a35c; --ambos: #a89cf2;
    --cov1: #143439; --cov2: #1b525b; --cov3: #2a7f8a; --cov4: #3fb0bc; --cov-ink: #e9f7f8;
    --r-base: #8d9ba7; --r-compite: #f07a6f; --r-combina: #66c48c; --r-contradice: #a89cf2;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0f151a; --surface: #161f26; --ink: #e3e9ee; --muted: #98a6b2; --line: #28343e;
  --accent: #48bfcb; --accent-ink: #062126; --accent-soft: #12333a;
  --hueco: #f07a6f; --hueco-soft: #3b1d1b; --nob: #e3ad4f; --nob-soft: #372a12;
  --sin: #6c7984; --sin-soft: #1d262e; --nuestro: #f0a35c; --ambos: #a89cf2;
  --cov1: #143439; --cov2: #1b525b; --cov3: #2a7f8a; --cov4: #3fb0bc; --cov-ink: #e9f7f8;
  --r-base: #8d9ba7; --r-compite: #f07a6f; --r-combina: #66c48c; --r-contradice: #a89cf2;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 var(--f-body); }
.envoltura { max-width: 1180px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
h1, h2, h3 { font-family: var(--f-display); text-wrap: balance; margin: 0; letter-spacing: -0.01em; }
h1 { font-size: clamp(1.7rem, 3.2vw, 2.4rem); font-weight: 700; }
h2 { font-size: 1.35rem; font-weight: 650; }
h3 { font-size: 1.05rem; font-weight: 600; }
p { margin: 0; }
.mono { font-family: var(--f-mono); font-size: .86em; }
.suave { color: var(--muted); }
.etiqueta { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 600; }
header.banda { background: var(--surface); border-bottom: 1px solid var(--line); }
header .envoltura { padding-block: 28px 22px; display: grid; gap: 8px; }
header .lema { max-width: 72ch; color: var(--muted); }
nav.pestanas { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg); border-bottom: 1px solid var(--line); }
nav.pestanas .envoltura { display: flex; gap: 4px; overflow-x: auto; padding-block: 8px; scrollbar-width: thin; }
nav.pestanas a { flex: none; text-decoration: none; color: var(--muted); font-weight: 500; padding: 7px 12px; border-radius: 7px; }
nav.pestanas a:hover { color: var(--ink); background: var(--sin-soft); }
nav.pestanas a[aria-current="page"] { color: var(--accent-ink); background: var(--accent); }
a:focus-visible, button:focus-visible, input:focus-visible, .nodo:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
main { padding-block: 26px 64px; }
section.vista { display: grid; gap: 22px; }
.intro { display: grid; gap: 6px; max-width: 78ch; }
.cifras { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.cifra { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 6px; align-content: start; }
.cifra .n { font-family: var(--f-display); font-size: 2rem; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
.barra { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--sin-soft); }
.barra span { display: block; height: 100%; }
.leyenda-mini { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: .78rem; color: var(--muted); }
.punto { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: 0; }
.dos { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; }
.bloque { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 18px; display: grid; gap: 12px; align-content: start; min-width: 0; }
.lista-huecos { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.lista-huecos li { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 10px; align-items: start; }
.prio { font-family: var(--f-mono); font-weight: 600; color: var(--hueco); font-variant-numeric: tabular-nums; }
.veredicto { border-left: 3px solid var(--nob); padding-left: 12px; }
.balanza { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.balanza div { border-radius: 8px; padding: 10px 12px; }
.balanza .favor { background: var(--accent-soft); } .balanza .contra { background: var(--nob-soft); }
.balanza .n { font-family: var(--f-display); font-size: 1.6rem; font-weight: 700; }
.aviso { background: var(--nob-soft); border-radius: 8px; padding: 10px 14px; font-size: .9rem; }
.chip { display: inline-flex; align-items: center; gap: 4px; font-size: .74rem; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: var(--sin-soft); color: var(--muted); white-space: nowrap; }
.chip.ok { background: var(--accent-soft); color: var(--accent); }
.chip.hueco { background: var(--hueco-soft); color: var(--hueco); }
.chip.nob { background: var(--nob-soft); color: var(--nob); }
.chip.nuestro { background: transparent; color: var(--nuestro); box-shadow: inset 0 0 0 1.5px var(--nuestro); }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.leyenda-matriz { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: .82rem; color: var(--muted); align-items: center; }
.muestra { display: inline-block; width: 16px; height: 12px; border-radius: 3px; vertical-align: -1px; margin-right: 6px; }
.matriz { display: grid; gap: 10px; }
.rejilla-scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
table.rejilla { border-collapse: separate; border-spacing: 3px; width: 100%; min-width: 720px; table-layout: fixed; }
table.rejilla th { font-weight: 600; font-size: .76rem; color: var(--muted); padding: 6px 4px; vertical-align: bottom; text-align: left; word-break: break-word; }
table.rejilla thead th { font-family: var(--f-mono); font-weight: 500; }
table.rejilla tbody th { font-family: var(--f-mono); font-weight: 500; width: 168px; vertical-align: middle; padding-left: 10px; }
table.rejilla th.esquina { width: 168px; font-family: var(--f-body); }
td.celda { padding: 0; }
td.celda button { all: unset; box-sizing: border-box; display: flex; flex-direction: column; gap: 3px; justify-content: center; width: 100%; min-height: 54px; padding: 6px 7px; border-radius: 6px; cursor: pointer; font-size: .72rem; line-height: 1.25; position: relative; }
td.celda button.c1 { background: var(--cov1); color: var(--cov-ink); } td.celda button.c2 { background: var(--cov2); color: var(--cov-ink); }
td.celda button.c3 { background: var(--cov3); color: #fff; } td.celda button.c4 { background: var(--cov4); color: #fff; }
td.celda button.hueco { background: var(--hueco-soft); color: var(--hueco); box-shadow: inset 0 0 0 1.5px var(--hueco); font-weight: 600; }
td.celda button.no_buscado { background: repeating-linear-gradient(135deg, var(--nob-soft) 0 6px, transparent 6px 10px); color: var(--nob); }
td.celda button.sin_sentido { background: var(--sin-soft); color: var(--sin); }
td.celda button[aria-pressed="true"] { outline: 2.5px solid var(--ink); outline-offset: 1px; }
td.celda .ids { font-family: var(--f-mono); font-weight: 500; word-break: break-word; }
td.celda .marca-n { position: absolute; top: 4px; right: 5px; width: 9px; height: 9px; border-radius: 50%; background: var(--nuestro); box-shadow: 0 0 0 2px var(--surface); }
.detalle { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 8px; }
.detalle ul { margin: 0; padding-left: 18px; display: grid; gap: 4px; }
.tarjetas { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.tarjeta { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px; display: grid; gap: 10px; align-content: start; min-width: 0; }
.tarjeta .cab { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
.mejor { background: var(--accent-soft); border-radius: 8px; padding: 10px 12px; display: grid; gap: 3px; }
.mejor .v { font-family: var(--f-display); font-weight: 650; font-size: 1.05rem; }
.resultados { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.resultados li { display: grid; gap: 2px; padding-top: 8px; border-top: 1px solid var(--line); }
.resultados .v { font-weight: 500; }
.resultados .c { font-size: .8rem; color: var(--muted); }
.fila-fuente { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: .78rem; }
.huecos { display: grid; gap: 14px; }
.hueco-t { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 18px; display: grid; grid-template-columns: 54px minmax(0, 1fr) 230px; gap: 16px; }
.hueco-t .num { font-family: var(--f-display); font-size: 2rem; font-weight: 700; color: var(--hueco); line-height: 1; }
.hueco-t .cuerpo { display: grid; gap: 9px; min-width: 0; }
.hueco-t ul { margin: 0; padding-left: 18px; display: grid; gap: 3px; font-size: .9rem; }
.medidores { display: grid; gap: 10px; align-content: start; }
.medidor { display: grid; gap: 4px; }
.pasos { display: flex; gap: 3px; } .pasos i { flex: 1; height: 6px; border-radius: 3px; background: var(--sin-soft); }
.pasos.dif i.on { background: var(--nob); } .pasos.rel i.on { background: var(--accent); }
.medidor small { color: var(--muted); font-size: .78rem; line-height: 1.35; }
.tarea { font-size: .9rem; border-top: 1px solid var(--line); padding-top: 8px; }
.chip.estrella { background: var(--nob-soft); color: var(--nob); }
.chip.encaje-directo { background: var(--accent); color: var(--accent-ink); }
.chip.encaje-prerrequisito { background: var(--accent-soft); color: var(--accent); }
.chip.encaje-diseño { background: var(--accent-soft); color: var(--ambos); }
.chip.encaje-complementario, .chip.encaje-lejano { background: var(--sin-soft); color: var(--muted); }
.encaje-txt { border-left: 3px solid var(--accent); padding-left: 10px; }
.encajes { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.encaje-grupo { display: grid; gap: 4px; align-content: start; padding: 10px 12px; border-radius: 8px; background: var(--bg); }
.encaje-grupo ul { margin: 0; padding-left: 16px; display: grid; gap: 3px; font-size: .88rem; }
.encaje-grupo.encaje-directo { box-shadow: inset 3px 0 0 var(--accent); }
.encaje-grupo.encaje-prerrequisito { box-shadow: inset 3px 0 0 var(--cov3); }
.encaje-grupo.encaje-diseño { box-shadow: inset 3px 0 0 var(--ambos); }
.encaje-grupo.encaje-complementario, .encaje-grupo.encaje-lejano { box-shadow: inset 3px 0 0 var(--sin); }
label.filtro { cursor: pointer; font-weight: 500; }
.lienzo { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow-x: auto; }
svg.grafo { display: block; width: 100%; min-width: 760px; height: auto; }
.arista { stroke-width: 1.7; opacity: .7; fill: none; }
.nodo text { fill: var(--ink); font: 500 11.5px var(--f-body); pointer-events: none; paint-order: stroke; stroke: var(--surface); stroke-width: 3.5px; }
.nodo circle { stroke: var(--surface); stroke-width: 2; cursor: pointer; }
.atenuado { opacity: .1; }
#flecha path { fill: var(--r-base); }
.tabla-scroll { overflow-x: auto; }
table.datos { border-collapse: collapse; width: 100%; font-size: .86rem; }
table.datos th, table.datos td { text-align: left; vertical-align: top; padding: 7px 8px; border-bottom: 1px solid var(--line); }
table.datos th { color: var(--muted); font-weight: 600; font-size: .78rem; }
td.cita { color: var(--muted); font-size: .8rem; }
details.plegable > summary { cursor: pointer; font-weight: 600; padding: 4px 0; }
.tres { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.tres .bloque ul { margin: 0; padding-left: 18px; display: grid; gap: 7px; font-size: .92rem; }
.tres .nuevo { border-top: 3px solid var(--accent); } .tres .existe { border-top: 3px solid var(--sin); } .tres .no { border-top: 3px solid var(--nob); }
.filtro { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.filtro input { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); width: min(340px, 100%); }
.papers { display: grid; gap: 10px; }
.paper { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 8px; }
.paper .cab { display: flex; gap: 10px; justify-content: space-between; align-items: start; flex-wrap: wrap; }
.paper .ejes { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 6px 14px; font-size: .82rem; }
.paper .ejes div { min-width: 0; }
@media (max-width: 900px) {
  .cifras { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dos, .tarjetas, .tres { grid-template-columns: minmax(0, 1fr); }
  .hueco-t { grid-template-columns: 40px minmax(0, 1fr); }
  .hueco-t .medidores { grid-column: 2; }
}
@media (max-width: 480px) { .cifras { grid-template-columns: minmax(0, 1fr); } .balanza { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: no-preference) { td.celda button { transition: filter .12s; } td.celda button:hover { filter: brightness(.96); } }
"""

JS = r"""
(function () {
  var D = JSON.parse(document.getElementById('datos').textContent);
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'class') e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c); });
    return e;
  }
  var ESC = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var bonito = function (v) { return String(v).replace(/_/g, ' '); };
  var PAPER = {}; D.papers.forEach(function (p) { PAPER[p.id] = p; });
  var nombreP = function (id) { var p = PAPER[id]; return p && p.corto ? id + ' · ' + p.corto : id; };
  var CONF = {leido_completo: ['leído completo', 'ok'], solo_resumen: ['solo resumen', 'nob'], insight_kairo: ['insight Kairo', ''], cita_sin_verificar: ['sin verificar', 'hueco'], repo: ['doc. del repo', 'ok'], brief: ['brief', 'nob']};
  var chipConf = function (c) { var x = CONF[c] || [c, '']; return el('span', {class: 'chip ' + x[1], text: x[0]}); };
  var MARCA = {hueco: ['hueco', 'hueco'], no_buscado: ['no buscado', 'nob'], sin_sentido: ['sin sentido', '']};
  var nuestroTxt = function (n) { return n.id + ' · ' + n.status; };
  var APUESTA = (D.meta && D.meta.apuesta) || 'la apuesta del proyecto';

  function resumen() {
    var R = D.resumen, v = (D.meta && D.meta.verificacion) || {};
    var cifras = el('div', {class: 'cifras'});
    var barra = el('div', {class: 'barra'}), ley = el('div', {class: 'leyenda-mini'});
    var colores = {leido_completo: 'var(--accent)', solo_resumen: 'var(--nob)', insight_kairo: 'var(--ambos)', cita_sin_verificar: 'var(--hueco)'};
    Object.keys(R.confianzas).forEach(function (k) {
      var n = R.confianzas[k]; if (!n) return;
      barra.appendChild(el('span', {style: 'width:' + (100 * n / R.n_papers) + '%;background:' + (colores[k] || 'var(--sin)'), title: (CONF[k] || [k])[0] + ': ' + n}));
      ley.appendChild(el('span', {}, [el('span', {class: 'punto', style: 'background:' + (colores[k] || 'var(--sin)')}), (CONF[k] || [k])[0] + ' ' + n]));
    });
    cifras.appendChild(el('div', {class: 'cifra'}, [el('span', {class: 'etiqueta', text: 'Papers en el mapa'}), el('span', {class: 'n', text: R.n_papers}), barra, ley]));
    cifras.appendChild(el('div', {class: 'cifra'}, [el('span', {class: 'etiqueta', text: 'Citas literales'}), el('span', {class: 'n', text: v.citas_total || 0}),
      el('span', {class: 'suave', style: 'font-size:.82rem', text: (v.citas_ok || 0) + ' coinciden con el texto del paper (' + (v.fecha || '') + ')'})]));
    cifras.appendChild(el('div', {class: 'cifra'}, [el('span', {class: 'etiqueta', text: 'Resultados con condiciones'}), el('span', {class: 'n', text: R.n_resultados}),
      el('span', {class: 'suave', style: 'font-size:.82rem', text: R.n_ideas + ' ideas y ' + R.n_relaciones + ' relaciones citadas'})]));
    var C = R.casillas, tot = C.cubiertas + C.hueco + C.no_buscado + C.sin_sentido;
    var b2 = el('div', {class: 'barra'});
    [['cubiertas', 'var(--cov3)'], ['hueco', 'var(--hueco)'], ['no_buscado', 'var(--nob)'], ['sin_sentido', 'var(--sin)']].forEach(function (x) {
      if (tot) b2.appendChild(el('span', {style: 'width:' + (100 * C[x[0]] / tot) + '%;background:' + x[1]}));
    });
    cifras.appendChild(el('div', {class: 'cifra'}, [el('span', {class: 'etiqueta', text: 'Casillas de las matrices'}), el('span', {class: 'n', text: tot}), b2,
      el('div', {class: 'leyenda-mini'}, [C.cubiertas + ' cubiertas', C.hueco + ' huecos', C.no_buscado + ' no buscadas', C.sin_sentido + ' sin sentido'])]));
    var top = el('ol', {class: 'lista-huecos'});
    D.huecos.slice(0, 5).forEach(function (h) {
      top.appendChild(el('li', {}, [el('span', {class: 'prio', text: String(h.prioridad == null ? '' : h.prioridad).padStart(2, '0')}),
        el('div', {}, [el('a', {href: '#huecos', text: (h.barato && h.barato.si ? '★ ' : '') + h.titulo, style: 'color:var(--ink);font-weight:600'}),
          el('p', {class: 'suave', style: 'font-size:.86rem', text: 'Encaje: ' + ((h.encaje || {}).nivel || '') + ' · Tarea: ' + (h.tarea || '')})])]));
    });
    var kids = [cifras];
    var izq = el('div', {class: 'bloque'}, [el('span', {class: 'etiqueta', text: 'Lo más prometedor para nosotros'}), el('h3', {text: 'Huecos priorizados'}), top]);
    var H = D.hipotesis_clave || {};
    if (H.enunciado) {
      var hip = el('div', {class: 'bloque'}, [el('span', {class: 'etiqueta', text: 'Hipótesis clave'}), el('h3', {text: H.enunciado}),
        el('div', {class: 'balanza'}, [
          el('div', {class: 'favor'}, [el('div', {class: 'n', text: (H.a_favor || []).length}), el('span', {text: 'evidencias a favor'})]),
          el('div', {class: 'contra'}, [el('div', {class: 'n', text: (H.en_contra || []).length}), el('span', {text: 'en contra o matizando'})])]),
        el('p', {class: 'veredicto', text: H.veredicto || ''}), el('a', {href: '#posicion', text: 'Ver la evidencia →'})]);
      kids.push(el('div', {class: 'dos'}, [izq, hip]));
    } else kids.push(izq);
    var nos = el('div', {class: 'chips'});
    D.hipotesis.forEach(function (h) { nos.appendChild(el('span', {class: 'chip nuestro', text: h.id + ' · ' + h.status + (h.frontier_cells.length ? '' : ' · sin casilla')})); });
    D.componentes.forEach(function (c) { nos.appendChild(el('span', {class: 'chip nuestro', text: c.id + ' · ' + (c.estado || '')})); });
    kids.push(el('div', {class: 'bloque'}, [el('span', {class: 'etiqueta', text: 'Nuestro proyecto en el mapa'}), nos,
      el('p', {class: 'suave', style: 'font-size:.9rem', text: 'En las matrices, un punto naranja marca dónde caen nuestras hipótesis y componentes. Lo nuestro no cuenta como cobertura.'})]));
    var O = D.obsoleto || {};
    var obs = [];
    if ((O.papers_sin_extraer || []).length) obs.push(O.papers_sin_extraer.length + ' papers del proyecto sin extraer');
    if ((O.taxonomia_cambiada || []).length) obs.push(O.taxonomia_cambiada.length + ' extracciones de una taxonomía anterior');
    if ((O.marcas_cubiertas || []).length) obs.push(O.marcas_cubiertas.length + ' casillas marcadas que un paper ya cubre');
    if (obs.length) kids.push(el('p', {class: 'aviso', text: 'Mapa desactualizado: ' + obs.join('; ') + '.'}));
    if (D.meta && D.meta.aviso) kids.push(el('p', {class: 'aviso', text: D.meta.aviso}));
    return el('section', {class: 'vista', id: 'v-resumen'}, kids);
  }

  function matrices() {
    var sec = el('section', {class: 'vista', id: 'v-matrices'}, [el('div', {class: 'intro'}, [el('h2', {text: 'Matrices de cobertura'}),
      el('p', {class: 'suave', text: 'Cada casilla cruza dos ejes. El color dice cuántos papers la cubren; las vacías llevan su marca. Pulsa una casilla para ver quién la cubre o por qué está vacía.'})]),
      el('div', {class: 'leyenda-matriz'}, [
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--cov1)'}), '1 paper']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--cov2)'}), '2']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--cov3)'}), '3']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--cov4)'}), '4 o más']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--hueco-soft);box-shadow:inset 0 0 0 1.5px var(--hueco)'}), 'hueco']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:repeating-linear-gradient(135deg,var(--nob-soft) 0 4px,transparent 4px 7px);box-shadow:inset 0 0 0 1px var(--nob)'}), 'no buscado']),
        el('span', {}, [el('span', {class: 'muestra', style: 'background:var(--sin-soft)'}), 'sin sentido']),
        el('span', {}, [el('span', {class: 'punto', style: 'background:var(--nuestro)'}), 'nosotros'])])]);
    D.matrices.forEach(function (m) {
      var det = el('div', {class: 'detalle'}, [el('p', {class: 'suave', text: 'Pulsa una casilla.'})]);
      var thead = el('tr', {}, [el('th', {class: 'esquina', text: m.fila_eje_nombre + ' ↓ / ' + m.col_eje_nombre + ' →'})]);
      m.cols.forEach(function (c) { thead.appendChild(el('th', {title: c.desc, text: bonito(c.v)})); });
      var tbody = el('tbody'), botones = [];
      m.filas.forEach(function (f, i) {
        var tr = el('tr', {}, [el('th', {title: f.desc, text: bonito(f.v)})]);
        m.cols.forEach(function (c, j) {
          var x = m.celdas[i][j], cls, contenido = [];
          if (x.papers.length) {
            cls = 'c' + Math.min(4, x.papers.length);
            contenido.push(el('span', {class: 'ids', text: x.papers.map(function (p) { return p.replace('P-', ''); }).join(' · ')}));
          } else {
            cls = x.marca;
            contenido.push(el('span', {text: (MARCA[x.marca] || [x.marca])[0]}));
          }
          if (x.nuestros.length) contenido.push(el('span', {class: 'marca-n', title: 'nuestro: ' + x.nuestros.map(nuestroTxt).join(', ')}));
          var etiqueta = bonito(f.v) + ' × ' + bonito(c.v) + ': ' + (x.papers.length ? x.papers.length + ' papers' : (MARCA[x.marca] || [x.marca])[0]);
          var b = el('button', {class: cls, type: 'button', 'aria-pressed': 'false', 'aria-label': etiqueta, 'data-celda': x.id, title: x.papers.length ? x.papers.map(nombreP).join(', ') : (x.por_que || '')}, contenido);
          b.addEventListener('click', function () {
            botones.forEach(function (o) { o.setAttribute('aria-pressed', 'false'); });
            b.setAttribute('aria-pressed', 'true');
            det.innerHTML = '';
            det.appendChild(el('span', {class: 'etiqueta', text: x.id}));
            if (x.papers.length) {
              var ul = el('ul');
              x.papers.forEach(function (p) {
                var P = PAPER[p] || {};
                ul.appendChild(el('li', {}, [el('strong', {class: 'mono', text: p}), ' ' + (P.corto ? P.corto + ' — ' : '') + (P.titulo || '') + (P.anio ? ' (' + P.anio + ') ' : ' '), chipConf(P.confianza)]));
              });
              det.appendChild(ul);
            } else {
              det.appendChild(el('p', {}, [el('span', {class: 'chip ' + (MARCA[x.marca] || ['', ''])[1], text: (MARCA[x.marca] || [x.marca])[0]}), ' ', x.por_que || '']));
            }
            if (x.nuestros.length) det.appendChild(el('p', {}, [el('span', {class: 'chip nuestro', text: 'nosotros'}), ' ' + x.nuestros.map(function (n) { return n.id + ' (' + n.tipo + ', ' + n.status + ')'; }).join(', ')]));
          });
          botones.push(b);
          tr.appendChild(el('td', {class: 'celda'}, [b]));
        });
        tbody.appendChild(tr);
      });
      var intro = [el('h3', {text: m.id + '. ' + m.titulo}), el('p', {class: 'suave', text: m.pregunta})];
      if (m.filtro) intro.push(el('p', {class: 'suave', style: 'font-size:.85rem', text: 'Solo papers con ' + m.filtro.eje + ' en ' + m.filtro.valores.join(', ') + '.'}));
      sec.appendChild(el('div', {class: 'matriz', id: 'matriz-' + m.id}, [el('div', {class: 'intro'}, intro),
        el('div', {class: 'rejilla-scroll'}, [el('table', {class: 'rejilla'}, [el('thead', {}, [thead]), tbody])]), det]));
    });
    sec.appendChild(el('p', {class: 'suave', style: 'font-size:.85rem', text: 'Un paper con varios valores en un eje cubre todas sus combinaciones, aunque no las haya probado juntas.'}));
    return sec;
  }

  function frontera() {
    var t = el('div', {class: 'tarjetas'});
    D.frontera.forEach(function (f) {
      var mejor = f.filas.filter(function (r) { return r.mejor; })[0];
      var ul = el('ul', {class: 'resultados'});
      f.filas.forEach(function (r) {
        if (r.mejor) return;
        var cond = Object.keys(r.condiciones).map(function (k) { return k + ': ' + r.condiciones[k]; }).join(' · ');
        ul.appendChild(el('li', {}, [el('span', {class: 'v', text: r.valor}), el('span', {class: 'c', text: cond}),
          el('div', {class: 'fila-fuente'}, [el('span', {class: 'mono', text: r.fuente}), chipConf(r.confianza)].concat(r.nota ? [el('span', {class: 'suave', text: r.nota})] : []))]));
      });
      var kids = [el('div', {class: 'cab'}, [el('h3', {text: f.aspecto}), el('span', {class: 'chip ' + (f.comparables ? 'ok' : 'nob'), text: f.comparables ? 'comparables' : 'no comparables'})])];
      if (mejor) {
        var c2 = Object.keys(mejor.condiciones).map(function (k) { return k + ': ' + mejor.condiciones[k]; }).join(' · ');
        kids.push(el('div', {class: 'mejor'}, [el('span', {class: 'etiqueta', text: 'Mejor conocido'}), el('span', {class: 'v', text: mejor.valor}),
          el('span', {class: 'suave', style: 'font-size:.8rem', text: c2}), el('div', {class: 'fila-fuente'}, [el('span', {class: 'mono', text: mejor.fuente}), chipConf(mejor.confianza)])]));
      } else kids.push(el('p', {class: 'etiqueta', text: 'Sin un mejor elegible'}));
      kids.push(ul, el('p', {class: 'suave', style: 'font-size:.86rem', text: f.nota}));
      t.appendChild(el('article', {class: 'tarjeta'}, kids));
    });
    return el('section', {class: 'vista', id: 'v-frontera'}, [el('div', {class: 'intro'}, [el('h2', {text: 'Frontera: el mejor resultado conocido'}),
      el('p', {class: 'suave', text: 'Cada cifra está en una cita literal del paper. Cuando las condiciones no permiten comparar, no se elige un ganador.'})]), t]);
  }

  function nivel(txt) {
    var w = String(txt || '').split(/[ ;(]/)[0].toLowerCase();
    return {baja: 1, 'media-alta': 3, media: 2, alta: 3, 'máxima': 4}[w] || 0;
  }
  function medidor(nombre, txt, clase) {
    var n = nivel(txt), pasos = el('div', {class: 'pasos ' + clase});
    for (var i = 1; i <= 4; i++) pasos.appendChild(el('i', {class: i <= n ? 'on' : ''}));
    return el('div', {class: 'medidor'}, [el('span', {class: 'etiqueta', text: nombre}), pasos, el('small', {text: txt || '—'})]);
  }
  function huecos() {
    var cont = el('div', {class: 'huecos'});
    D.huecos.forEach(function (h) {
      var barato = h.barato || {}, encaje = h.encaje || {};
      var ev = el('ul');
      h.evidencia.forEach(function (e) { ev.appendChild(el('li', {}, [el('span', {class: 'mono', text: (e.ref || '') + (e.loc ? ' ' + e.loc : '')}), ' — ' + (e.texto || '')])); });
      var celdas = el('div', {class: 'chips'});
      h.celdas.forEach(function (c) {
        celdas.appendChild(c.cubierta_por.length ? el('span', {class: 'chip hueco', text: '⚠ ' + c.celda + ' ya cubierta por ' + c.cubierta_por.join(', ')})
          : el('span', {class: 'chip ' + (MARCA[c.marca] || ['', ''])[1], text: c.celda.replace('|', ' × ') + ' · ' + (MARCA[c.marca] || [c.marca])[0]}));
      });
      h.atacado_por.forEach(function (a) { celdas.appendChild(el('span', {class: 'chip nuestro', text: a.id + ' · ' + a.status})); });
      var etiquetas = el('div', {class: 'chips'}, [
        el('span', {class: 'chip ' + (barato.si ? 'estrella' : ''), title: barato.por_que || '', text: barato.si ? '★ poco cómputo' : 'cómputo alto'}),
        el('span', {class: 'chip encaje-' + encaje.nivel, title: (D.niveles_encaje || {})[encaje.nivel] || '', text: 'Encaje: ' + (encaje.nivel || '')})]);
      var cuerpo = [el('h3', {text: (barato.si ? '★ ' : '') + h.titulo}), etiquetas,
        el('p', {class: 'encaje-txt'}, [el('strong', {text: 'Encaje: '}), encaje.texto || '']),
        el('p', {class: 'suave', style: 'font-size:.86rem'}, [el('strong', {text: 'Coste: '}), barato.por_que || '']),
        el('p', {text: h.por_que}), ev];
      if (h.celdas.length || h.atacado_por.length) cuerpo.push(celdas);
      if (h.realidad) cuerpo.push(el('p', {class: 'suave', style: 'font-size:.88rem'}, [el('strong', {text: '¿Real? '}), h.realidad]));
      cuerpo.push(el('p', {class: 'tarea'}, [el('strong', {text: 'Lo ataca: '}), h.tarea || '']));
      cont.appendChild(el('article', {class: 'hueco-t', 'data-barato': barato.si ? '1' : '0'}, [
        el('div', {class: 'num', text: String(h.prioridad == null ? '' : h.prioridad).padStart(2, '0')}),
        el('div', {class: 'cuerpo'}, cuerpo),
        el('div', {class: 'medidores'}, [medidor('Relevancia', h.relevancia, 'rel'), medidor('Dificultad', h.dificultad, 'dif')])]));
    });
    var niveles = D.niveles_encaje || {}, grupos = el('div', {class: 'encajes'});
    Object.keys(niveles).forEach(function (n) {
      var hs = D.huecos.filter(function (h) { return (h.encaje || {}).nivel === n; });
      if (!hs.length) return;
      var ul = el('ul');
      hs.forEach(function (h) { ul.appendChild(el('li', {text: (h.barato && h.barato.si ? '★ ' : '') + h.titulo})); });
      grupos.appendChild(el('div', {class: 'encaje-grupo encaje-' + n}, [el('span', {class: 'etiqueta', text: n}), el('p', {class: 'suave', style: 'font-size:.82rem', text: niveles[n]}), ul]));
    });
    var solo = el('input', {type: 'checkbox', id: 'solo-baratos'});
    solo.addEventListener('change', function () {
      cont.querySelectorAll('.hueco-t').forEach(function (a) { a.hidden = solo.checked && a.dataset.barato !== '1'; });
    });
    var nb = D.huecos.filter(function (h) { return h.barato && h.barato.si; }).length;
    return el('section', {class: 'vista', id: 'v-huecos'}, [el('div', {class: 'intro'}, [el('h2', {text: 'Huecos priorizados'}),
      el('p', {class: 'suave', text: 'Por prioridad para nosotros. ★ = se puede comprobar con poco cómputo. Las casillas se recalculan al regenerar: si un paper nuevo cubre una, aparece un aviso.'})]),
      el('div', {class: 'bloque'}, [el('span', {class: 'etiqueta', text: 'Cómo encajan con la apuesta'}), el('h3', {text: APUESTA}), grupos]),
      el('label', {class: 'filtro', for: 'solo-baratos'}, [solo, 'Ver solo los ' + nb + ' huecos con ★']), cont]);
  }

  var COL = {hecho: 'var(--accent)', nuestro: 'var(--nuestro)', ambos: 'var(--ambos)', sin_explorar: 'var(--sin)'};
  var ETQ = {hecho: 'Hecho por otros', nuestro: 'Solo nosotros', ambos: 'Otros y nosotros', sin_explorar: 'Sin explorar'};
  var REL = {se_basa_en: ['var(--r-base)', ''], compite_con: ['var(--r-compite)', '6 4'], se_combina_con: ['var(--r-combina)', ''], contradice: ['var(--r-contradice)', '2 3']};
  function ideas() {
    var N = {}; D.ideas.nodos.forEach(function (n) { N[n.id] = n; });
    var s = '<defs><marker id="flecha" viewBox="0 0 10 10" refX="17" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>';
    D.ideas.aristas.forEach(function (a) {
      var p = N[a.de], q = N[a.a], r = REL[a.tipo] || REL.se_basa_en;
      if (!p || !q) return;
      var cit = a.citas.map(function (c) { return c.paper + ' ' + (c.loc || ''); }).join('; ');
      s += '<line class="arista" data-de="' + ESC(a.de) + '" data-a="' + ESC(a.a) + '" x1="' + p.x + '" y1="' + p.y + '" x2="' + q.x + '" y2="' + q.y + '" style="stroke:' + r[0] + '" stroke-dasharray="' + r[1] + '"' + (a.tipo === 'se_basa_en' ? ' marker-end="url(#flecha)"' : '') + '><title>' + ESC(p.nombre + ' ' + bonito(a.tipo) + ' ' + q.nombre + ' — ' + cit + (a.origen === 'deducida' ? ' (relación deducida)' : '')) + '</title></line>';
    });
    D.ideas.nodos.forEach(function (n) {
      var quien = n.papers.concat(n.nuestros).join(', ') || 'nadie en el corpus';
      s += '<g class="nodo" data-id="' + ESC(n.id) + '" tabindex="0"><circle cx="' + n.x + '" cy="' + n.y + '" r="9" style="fill:' + COL[n.estado] + '"/><text x="' + n.x + '" y="' + (n.y - 14) + '" text-anchor="middle">' + ESC(n.nombre) + '</text><title>' + ESC(n.nombre + ' — ' + ETQ[n.estado] + '. ' + n.desc + ' Usada por: ' + quien) + '</title></g>';
    });
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'grafo'); svg.setAttribute('viewBox', '0 0 1000 760'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Grafo de ideas y relaciones');
    svg.innerHTML = s;
    var ley = el('div', {class: 'leyenda-matriz'});
    Object.keys(COL).forEach(function (k) { ley.appendChild(el('span', {}, [el('span', {class: 'punto', style: 'background:' + COL[k]}), ETQ[k]])); });
    Object.keys(REL).forEach(function (k) { ley.appendChild(el('span', {style: 'color:' + REL[k][0]}, ['— ' + bonito(k)])); });
    var filas = el('tbody');
    D.ideas.nodos.slice().sort(function (a, b) { return Object.keys(ETQ).indexOf(a.estado) - Object.keys(ETQ).indexOf(b.estado) || a.nombre.localeCompare(b.nombre); }).forEach(function (n) {
      filas.appendChild(el('tr', {}, [el('td', {}, [el('span', {class: 'punto', style: 'background:' + COL[n.estado]}), n.nombre]), el('td', {text: ETQ[n.estado]}),
        el('td', {class: 'mono', text: n.papers.join(', ') || '—'}), el('td', {text: n.nuestros.join(', ') || '—'})]));
    });
    var rels = el('tbody');
    D.ideas.aristas.forEach(function (a) {
      var td = el('td', {class: 'cita'});
      a.citas.forEach(function (c, i) { if (i) td.appendChild(el('br')); td.appendChild(document.createTextNode(c.paper + ' ' + (c.loc || '') + (c.cita ? ': «' + c.cita + '»' : ''))); });
      if (a.nota) { td.appendChild(el('br')); td.appendChild(el('em', {text: a.nota})); }
      rels.appendChild(el('tr', {}, [el('td', {text: (N[a.de] || {nombre: a.de}).nombre}), el('td', {text: bonito(a.tipo)}), el('td', {text: (N[a.a] || {nombre: a.a}).nombre}), el('td', {text: a.origen === 'autores' ? 'autores' : 'deducida'}), td]));
    });
    var sec = el('section', {class: 'vista', id: 'v-ideas'}, [el('div', {class: 'intro'}, [el('h2', {text: 'Mapa de ideas'}),
      el('p', {class: 'suave', text: 'El color dice quién usa cada idea; cada línea es una relación citada. Pasa el ratón o enfoca un nodo para aislar sus relaciones.'})]),
      ley, el('div', {class: 'lienzo'}, [svg]),
      el('details', {class: 'plegable'}, [el('summary', {text: 'Ideas y quién las usa (' + D.ideas.nodos.length + ')'}),
        el('div', {class: 'tabla-scroll'}, [el('table', {class: 'datos'}, [el('thead', {}, [el('tr', {}, [el('th', {text: 'Idea'}), el('th', {text: 'Estado'}), el('th', {text: 'Papers'}), el('th', {text: 'Nosotros'})])]), filas])])]),
      el('details', {class: 'plegable'}, [el('summary', {text: 'Relaciones con sus citas (' + D.ideas.aristas.length + ')'}),
        el('div', {class: 'tabla-scroll'}, [el('table', {class: 'datos'}, [el('thead', {}, [el('tr', {}, [el('th', {text: 'De'}), el('th', {text: 'Relación'}), el('th', {text: 'A'}), el('th', {text: 'Origen'}), el('th', {text: 'Citas'})])]), rels])])])]);
    var nodos = svg.querySelectorAll('.nodo'), aristas = svg.querySelectorAll('.arista');
    function aislar(id) {
      var v = {}; v[id] = true;
      aristas.forEach(function (a) { var t = a.dataset.de === id || a.dataset.a === id; a.classList.toggle('atenuado', !t); if (t) { v[a.dataset.de] = true; v[a.dataset.a] = true; } });
      nodos.forEach(function (n) { n.classList.toggle('atenuado', !v[n.dataset.id]); });
    }
    function limpiar() { svg.querySelectorAll('.atenuado').forEach(function (e) { e.classList.remove('atenuado'); }); }
    nodos.forEach(function (n) { n.addEventListener('mouseenter', function () { aislar(n.dataset.id); }); n.addEventListener('focus', function () { aislar(n.dataset.id); }); n.addEventListener('mouseleave', limpiar); n.addEventListener('blur', limpiar); });
    return sec;
  }

  function posicion() {
    var P = D.posicionamiento || {}, H = D.hipotesis_clave || {};
    function lista(items) { var ul = el('ul'); (items || []).forEach(function (t) { ul.appendChild(el('li', {text: t})); }); return ul; }
    function ev(items) { var ul = el('ul'); (items || []).forEach(function (e) { ul.appendChild(el('li', {}, [el('span', {class: 'mono', text: (e.ref === 'meta' ? 'Corpus' : e.ref) + (e.loc ? ' ' + e.loc : '')}), ' — ' + (e.texto || '')])); }); return ul; }
    var filas = el('tbody');
    D.donde.forEach(function (d) {
      filas.appendChild(el('tr', {}, [el('td', {}, [el('span', {class: 'chip nuestro', text: d.id + ' · ' + d.tipo + ' · ' + d.status})]), el('td', {class: 'mono', text: d.matriz}),
        el('td', {text: bonito(d.fila) + ' × ' + bonito(d.col)}),
        el('td', {}, d.papers.length ? [el('span', {class: 'mono', text: d.papers.join(', ')})] : [el('span', {class: 'chip ' + (MARCA[d.marca] || ['', ''])[1], text: 'nadie · ' + (MARCA[d.marca] || [d.marca])[0]})])]));
    });
    var kids = [el('div', {class: 'intro'}, [el('h2', {text: 'Dónde caemos nosotros'}),
      el('p', {class: 'suave', text: 'Qué es nuevo respecto al corpus, qué ya existe y qué todavía no podemos afirmar.'})]),
      el('div', {class: 'tres'}, [
        el('div', {class: 'bloque nuevo'}, [el('h3', {text: 'Nuevo'}), lista(P.nuevo)]),
        el('div', {class: 'bloque existe'}, [el('h3', {text: 'Ya existe'}), lista(P.ya_existe)]),
        el('div', {class: 'bloque no'}, [el('h3', {text: 'No podemos afirmar'}), lista(P.no_podemos_afirmar)])])];
    if (H.enunciado) kids.push(el('div', {class: 'bloque'}, [el('span', {class: 'etiqueta', text: 'Hipótesis clave'}), el('h3', {text: H.enunciado}),
      el('div', {class: 'dos'}, [el('div', {}, [el('p', {class: 'etiqueta', text: 'A favor'}), ev(H.a_favor)]), el('div', {}, [el('p', {class: 'etiqueta', text: 'En contra o matizando'}), ev(H.en_contra)])]),
      el('p', {class: 'veredicto', text: H.veredicto || ''})]));
    kids.push(el('div', {class: 'bloque'}, [el('h3', {text: 'Nuestras casillas en las matrices'}),
      el('div', {class: 'tabla-scroll'}, [el('table', {class: 'datos'}, [el('thead', {}, [el('tr', {}, [el('th', {text: 'Nuestro'}), el('th', {text: 'Matriz'}), el('th', {text: 'Casilla'}), el('th', {text: 'Ya la cubren'})])]), filas])])]));
    return el('section', {class: 'vista', id: 'v-posicion'}, kids);
  }

  function papers() {
    var lista = el('div', {class: 'papers'});
    D.papers.forEach(function (p) {
      var ejes = el('div', {class: 'ejes'});
      D.ejes.forEach(function (e) {
        var v = p.ejes[e.id]; v = Array.isArray(v) ? v.map(bonito).join(', ') : (v == null ? 'no consta' : bonito(v));
        ejes.appendChild(el('div', {}, [el('span', {class: 'etiqueta', text: e.nombre + ' '}), document.createTextNode(v)]));
      });
      var res = el('ul', {style: 'margin:0;padding-left:18px;font-size:.88rem;display:grid;gap:3px'});
      p.resultados.forEach(function (r) { res.appendChild(el('li', {}, [(r.valor || '') + ' ', el('span', {class: 'mono suave', text: r.loc || ''})])); });
      var lim = el('ul', {style: 'margin:0;padding-left:18px;font-size:.86rem;display:grid;gap:3px'});
      p.limitaciones.forEach(function (l) { lim.appendChild(el('li', {}, [el('span', {class: 'chip ' + (l.origen === 'autores' ? '' : 'nob'), text: l.origen === 'autores' ? 'autores' : 'deducida'}), ' ' + (l.texto || '')])); });
      var notas = Object.keys(p.notas).map(function (k) { return el('p', {class: 'suave', style: 'font-size:.82rem', text: k + ': ' + p.notas[k]}); });
      var kids = [el('div', {class: 'cab'}, [el('div', {}, [el('span', {class: 'mono', style: 'font-weight:600', text: p.id + (p.corto ? ' · ' + p.corto : '')}), el('h3', {text: (p.titulo || '') + (p.anio ? ' (' + p.anio + ')' : '')})]), chipConf(p.confianza)])];
      if (p.cobertura) kids.push(el('p', {class: 'suave', style: 'font-size:.84rem', text: p.cobertura}));
      kids.push(ejes);
      kids = kids.concat(notas);
      if (p.resultados.length) kids.push(el('details', {class: 'plegable'}, [el('summary', {text: 'Resultados (' + p.resultados.length + ')'}), res]));
      if (p.limitaciones.length) kids.push(el('details', {class: 'plegable'}, [el('summary', {text: 'Limitaciones (' + p.limitaciones.length + ')'}), lim]));
      lista.appendChild(el('article', {class: 'paper', 'data-busqueda': (p.id + ' ' + (p.titulo || '') + ' ' + (p.corto || '') + ' ' + JSON.stringify(p.ejes)).toLowerCase()}, kids));
    });
    var input = el('input', {id: 'filtro-papers', type: 'search', placeholder: 'Filtrar: id, título, valor de un eje…', 'aria-label': 'Filtrar papers'});
    var cuenta = el('span', {class: 'suave', text: D.papers.length + ' papers'});
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase(), n = 0;
      lista.querySelectorAll('.paper').forEach(function (a) { var ok = !q || a.dataset.busqueda.indexOf(q) >= 0; a.hidden = !ok; if (ok) n++; });
      cuenta.textContent = n + ' papers';
    });
    return el('section', {class: 'vista', id: 'v-papers'}, [el('div', {class: 'intro'}, [el('h2', {text: 'Papers'}),
      el('p', {class: 'suave', text: 'Los ' + D.papers.length + ' papers del mapa con sus ejes, resultados y limitaciones. La evidencia literal de cada asignación está en extracciones/.'})]),
      el('div', {class: 'filtro'}, [input, cuenta]), lista]);
  }

  var VISTAS = {resumen: resumen, matrices: matrices, frontera: frontera, huecos: huecos, ideas: ideas, posicion: posicion, papers: papers};
  var main = $('main .envoltura'), hechas = {};
  function mostrar() {
    var id = (location.hash || '#resumen').slice(1); if (!VISTAS[id]) id = 'resumen';
    Object.keys(VISTAS).forEach(function (k) {
      if (k === id && !hechas[k]) { hechas[k] = VISTAS[k](); main.appendChild(hechas[k]); }
      if (hechas[k]) hechas[k].hidden = k !== id;
    });
    document.querySelectorAll('nav.pestanas a').forEach(function (a) { if (a.getAttribute('href') === '#' + id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  }
  window.addEventListener('hashchange', function () { mostrar(); window.scrollTo(0, 0); });
  mostrar();
})();
"""

PESTANAS = [("resumen", "Resumen"), ("matrices", "Matrices"), ("frontera", "Frontera"), ("huecos", "Huecos"),
            ("ideas", "Ideas"), ("posicion", "Posicionamiento"), ("papers", "Papers")]


def vista_html(datos: dict) -> str:
    """mapa.html: mapa.json embedded and drawn by the page's own script."""
    blob = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")
    esc = html.escape
    nav = "".join(f'<a href="#{k}">{v}</a>' for k, v in PESTANAS)
    fecha = datos.get("generado") or ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Mapa de la frontera</title>
<!-- Generado por scripts/frontier/frontier.py a partir de mapa.yaml. No editar a mano. -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>
</head>
<body>
<header class="banda"><div class="envoltura">
  <span class="etiqueta">{esc(str(datos.get('proyecto') or ''))} · frontera del estado del arte · {esc(str(fecha))}</span>
  <h1>{esc(str(datos.get('nombre') or ''))}</h1>
  <p class="lema">Qué está hecho, el mejor resultado conocido en cada aspecto y qué huecos quedan, a partir de los {datos['resumen']['n_papers']} papers del mapa. Escrito por un modelo: no es citable; cita los papers.</p>
</div></header>
<nav class="pestanas" aria-label="Secciones"><div class="envoltura">{nav}</div></nav>
<main><div class="envoltura"></div></main>
<script type="application/json" id="datos">{blob}</script>
<script>{JS}</script>
</body>
</html>
"""
