#!/usr/bin/env python3
"""Notice drafts by mail-merge: one draft per affected repository, and no model
ever sees who is notified or what was found.

    notices.py template --vault V --project P --name N [--kind afectado|vendor] [--new]
    notices.py draft    --vault V --project P --dataset NAME --template N --out DIR
                        [--embargo-days 90] [--var k=v ...] [--vars-file F]
    notices.py vendor   --vault V --project P --template N --destinatario "…" --canal "…"
                        --out DIR [--embargo-days 90] [--var k=v ...] [--vars-file F]

`template` is the one an agent may run. Templates live in
`Projects/<slug>/Plantillas-avisos/<name>.md` (NOT under `Avisos/`, which the data
guard keeps away from every model), so the agent can help the researcher write the
wording. A template holds wording and `{{marcadores}}`, never real data. With `--new`
it writes a skeleton (refused when the file exists); then it always checks it:
frontmatter `tipo: plantilla-aviso` and `kind`, a `## Mensaje` section, no secret
(redact.py), no e-mail address outside example.org / example.com / example.net, no
`github.com/<owner>/<repo>` link, and no finding placeholder ({{files}}, {{hallazgo}},
{{hallazgos}}, {{n_hallazgos}}) in `## Primer contacto` — the public text of an issue.
Placeholders that are neither built in nor provided by the data are variables that
`draft` / `vendor` must receive (`--var`, `--vars-file`); the check lists their names.

Built-in placeholders:
  both kinds  {{id_aviso}} {{canal}} {{fecha}} {{fecha_limite}} {{embargo_dias}}
  afectado    {{repo}} {{files}} (one `- path` line each) {{hallazgo}} (one `- kind (n)`
              line per kind) {{hallazgos}} (one `- path: kind` line per finding)
              {{n_hallazgos}}
  vendor      {{destinatario}}
Anything else, {{contacto_investigador}} for instance, is a variable.

`draft` and `vendor` are the researcher's commands: they write
`Projects/<slug>/Avisos/registro.json`, so they refuse to run inside an agent session
(`CLAUDECODE` / `KAIRO_AGENT_SESSION`, as study_status.py does for researcher-only
transitions). `draft` reads the registered findings dataset NAME (a JSONL `file`
dataset of `Datos/registro.json`; one finding per line: `repo` (required), `files`
(string or list), `kind`, and the public metadata `private_vulnerability_reporting`,
`security_policy_url`, `security_email`), groups the findings by repository and
renders one draft per repository into DIR. The suggested channel per repository:

  github_pvr          any finding says `private_vulnerability_reporting: true`
                      (the repository's private vulnerability report form);
  politica_seguridad  else a `security_policy_url`: follow the published policy (it
                      may name its own channel; the draft carries the link);
  correo: <address>   else a `security_email`;
  issue_sin_detalles  else: a public issue that only asks for a private contact. The
                      draft carries the `## Primer contacto` text (or a neutral default)
                      for the issue and the full notice to send once a contact answers.

Each new draft gets a pending entry appended to `Avisos/registro.json` (created as
`[]` when missing): `{id: AV-NNN (continuing the sequence), destinatario: <repo>,
canal, enviado: null, embargo_hasta: today + --embargo-days, respuesta: "", estado:
"pendiente"}`. A repository that already has an open entry (pendiente / enviado) is
skipped and counted. Nothing is committed: the app commits the registry.

DIR is sensitive: it must be absolute, outside the vault and outside any git working
tree, and empty or new. Register it as a sensitive dataset (panel Datos) so the data
guard keeps agents away from it; the output says whether it already is. Every rendered
draft is checked with redact.py before anything is written: a finding that carries a
secret value refuses the whole run (the drafts are not written; DIR/_problemas.txt
names the repositories). A placeholder left without a value refuses too.

stdout is counts only — never a repository, a path or a finding: `{borradores,
repositorios, hallazgos, lineas_invalidas, ya_abiertos, canales, avisos, embargo_hasta,
out_registrado_sensible, …}`.

Why nothing is ever sent from here: a notice goes to a third party about material
they exposed by accident, and it cannot be taken back. Sending from a script would put
the recipients and the findings within reach of whatever runs commands in the session
(an agent included), would act on a channel no person has checked (a security policy
may ask for something else, an address may be stale), and would start the embargo
without a human deciding the notice is right. The researcher reads each draft, sends
it by hand on its channel, and marks it `enviado` in the panel Avisos with the real
date (adjusting `embargo_hasta` there if sending happens later than the draft).

Exit codes: 0 ok · 3 refused (agent session, bad template, missing value, secret in
a draft, DIR not allowed, unreadable registry) · 1 error (no vault, unknown project
or dataset, bad arguments). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "security"))
import project_scan as ps  # noqa: E402
import redact  # noqa: E402
from notes import read_note, section, split_note  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/notices@{__version__}"
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")
TEMPLATES = "Plantillas-avisos"
KINDS = ("afectado", "vendor")
OPEN_STATES = ("pendiente", "enviado")
DEFAULT_EMBARGO = 90

COMMON = {"id_aviso", "canal", "fecha", "fecha_limite", "embargo_dias"}
BUILTIN = {"afectado": COMMON | {"repo", "files", "hallazgo", "hallazgos", "n_hallazgos"},
           "vendor": COMMON | {"destinatario"}}
FINDING_PLACEHOLDERS = {"files", "hallazgo", "hallazgos", "n_hallazgos"}

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$")
_VAR = re.compile(r"^[a-z_][a-z0-9_]*$")
_PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_EXAMPLE_DOMAINS = re.compile(r"(?:^|\.)example\.(?:org|com|net)$|\.(?:example|invalid|test)$", re.I)
_GH_REPO = re.compile(r"github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+", re.I)
_AV_ID = re.compile(r"^AV-(\d+)$")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")

DEFAULT_FIRST_CONTACT = (
    "Hello,\n\n"
    "While doing research we noticed something in this repository that we would like to "
    "report privately. Could you tell us a private channel (a security contact, an e-mail "
    "address, or enabling private vulnerability reporting) where we can send the details?\n\n"
    "We will not share any detail in public. Reference: {{id_aviso}}.\n\n"
    "Thank you.")

SKELETON = {
    "afectado": """---
tipo: plantilla-aviso
# kind — afectado: un repositorio con hallazgos del dataset (notices.py draft);
#        vendor: los mantenedores de una herramienta (notices.py vendor).
kind: afectado
# asunto — una línea; admite marcadores.
asunto: "Security notice for {{repo}} ({{id_aviso}})"
---

<!-- Plantilla de aviso. Sin datos reales: ni nombres, ni direcciones, ni enlaces a
repositorios concretos, ni valores. Los datos los pone `notices.py draft` en una carpeta
fuera del vault. Escribe el mensaje en el idioma del destinatario. -->

## Mensaje

Hello,

During a research study on {{repo}} we found material that looks sensitive in
these files:

{{files}}

Kind of finding:

{{hallazgo}}

We have not used it, we have not shared it, and we will not publish anything that
identifies this repository. We will keep this private until {{fecha_limite}}
({{embargo_dias}} days). Please remove it from the history and rotate whatever it
gives access to.

Contact: {{contacto_investigador}} — reference {{id_aviso}}.

## Primer contacto

Hello,

We would like to report something about this repository privately. Could you tell us
a private channel (a security contact or private vulnerability reporting)?
Reference: {{id_aviso}}. Contact: {{contacto_investigador}}.
""",
    "vendor": """---
tipo: plantilla-aviso
# kind — vendor: los mantenedores de una herramienta (divulgación coordinada).
kind: vendor
asunto: "Coordinated disclosure ({{id_aviso}})"
---

<!-- Plantilla de aviso a mantenedores. Sin datos reales. Lo concreto (versiones,
pasos, impacto) va en variables que el investigador da a `notices.py vendor`. -->

## Mensaje

Hello {{destinatario}},

We would like to report, under coordinated disclosure, an issue we found while
studying how the tool is used: {{resumen}}

We propose to keep it private until {{fecha_limite}} ({{embargo_dias}} days).

Contact: {{contacto_investigador}} — reference {{id_aviso}}.
""",
}


class Refused(Exception):
    """exit 3"""


class InputError(Exception):
    """exit 1"""


# --------------------------------------------------------------------------- #
# Paths and the vault
# --------------------------------------------------------------------------- #

def _key(p: Path) -> str:
    return str(p).replace("\\", "/").rstrip("/").casefold()


def inside(p: Path, root: Path) -> bool:
    kp, kr = _key(p), _key(root)
    return kp == kr or kp.startswith(kr + "/")


def _resolve(p: Path | str) -> Path:
    try:
        return Path(p).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(Path(p).expanduser()))


def git_tree(p: Path) -> Path | None:
    for a in (p, *p.parents):
        if (a / ".git").exists():
            return a
    return None


def project_dir(vault: Path, project: str) -> Path:
    if not vault.is_dir():
        raise InputError(f"no existe el vault {vault}")
    try:
        return ps.resolve_project(vault, project)[0]
    except ps.InputError as exc:
        raise InputError(str(exc)) from None


def agent_marker(environ: Mapping[str, str]) -> str | None:
    for m in AGENT_MARKERS:
        if environ.get(m):
            return m
    return None


def load_datasets(pdir: Path) -> list[dict]:
    reg = pdir / "Datos" / "registro.json"
    if not reg.is_file():
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused("Datos/registro.json ilegible: corrígelo desde el panel Datos") from None
    if not isinstance(data, list):
        raise Refused("Datos/registro.json no es una lista")
    return [d for d in data if isinstance(d, dict)]


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

def template_path(pdir: Path, name: str) -> Path:
    if not _NAME.match(name or ""):
        raise InputError("--name / --template: minúsculas, dígitos, `-` y `_` (hasta 61), sin extensión")
    return pdir / TEMPLATES / f"{name}.md"


def load_template(path: Path) -> dict:
    """{kind, asunto, mensaje, primer_contacto, text}; Refused when it cannot be parsed."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise InputError(f"no existe la plantilla {path.parent.name}/{path.name}") from None
    except (OSError, UnicodeDecodeError):
        raise Refused(f"plantilla ilegible: {path.parent.name}/{path.name}") from None
    parsed = read_note(path)
    if parsed is None or split_note(text) is None:
        raise Refused("la plantilla no tiene frontmatter (---)")
    fm, body = parsed
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return {"tipo": str(fm.get("tipo") or "").strip(), "kind": str(fm.get("kind") or "").strip(),
            "asunto": str(fm.get("asunto") or "").strip(), "mensaje": section(body, "Mensaje"),
            "primer_contacto": section(body, "Primer contacto"), "text": text}


def placeholders(s: str) -> list[str]:
    return list(dict.fromkeys(_PH.findall(s)))


def check_template(tpl: dict) -> dict:
    """{ok, problemas, marcadores, variables}. Problems never quote the template's text
    beyond a placeholder's name."""
    problems: list[str] = []
    if tpl["tipo"] != "plantilla-aviso":
        problems.append("frontmatter: falta `tipo: plantilla-aviso`")
    kind = tpl["kind"]
    if kind not in KINDS:
        problems.append(f"frontmatter: `kind` debe ser {' | '.join(KINDS)}")
        kind = "afectado"
    if not tpl["mensaje"]:
        problems.append("falta la sección `## Mensaje` o está vacía")
    if kind == "vendor" and tpl["primer_contacto"]:
        problems.append("`## Primer contacto` es solo para avisos `afectado`")
    leaked = sorted(set(placeholders(tpl["primer_contacto"])) & FINDING_PLACEHOLDERS)
    if leaked:
        problems.append("`## Primer contacto` es texto público (un issue): no puede llevar "
                        + ", ".join("{{" + n + "}}" for n in leaked))
    text = tpl["text"]
    for f in redact.redact_text(text)[1]:
        problems.append(f"material privado ({f['type']} sha256:{f['sha256']}): quítalo; la plantilla no lleva datos")
    emails = [m.group(0) for m in _EMAIL.finditer(text) if not _EXAMPLE_DOMAINS.search(m.group(1))]
    if emails:
        problems.append(f"{len(emails)} dirección(es) de correo reales: usa una variable "
                        "({{contacto_investigador}}) o un dominio example.org")
    if _GH_REPO.search(text):
        problems.append("enlace a un repositorio concreto de GitHub: la plantilla no nombra a nadie; usa {{repo}}")
    found = placeholders(tpl["asunto"] + "\n" + tpl["mensaje"] + "\n" + tpl["primer_contacto"])
    bad = [n for n in found if not _VAR.match(n)]
    if bad:
        problems.append("marcadores con mayúsculas: " + ", ".join(bad) + " (usa minúsculas)")
    other = BUILTIN["vendor" if kind == "afectado" else "afectado"] - BUILTIN[kind]
    wrong = [n for n in found if n in other]
    if wrong:
        problems.append(f"marcadores que un aviso `{kind}` no tiene: " + ", ".join(wrong))
    variables = [n for n in found if n not in BUILTIN[kind] and n not in other and _VAR.match(n)]
    return {"ok": not problems, "kind": kind, "problemas": problems, "marcadores": found, "variables": variables}


def render(s: str, values: Mapping[str, str]) -> str:
    return _PH.sub(lambda m: values.get(m.group(1), m.group(0)), s)


# --------------------------------------------------------------------------- #
# Variables, out dir, registry
# --------------------------------------------------------------------------- #

def parse_vars(pairs: list[str], vars_file: str | None, kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if vars_file:
        try:
            data = json.loads(Path(vars_file).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            raise InputError("--vars-file: no es un JSON legible") from None
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise InputError("--vars-file: debe ser un objeto JSON de cadenas")
        out.update({str(k): v for k, v in data.items()})
    for p in pairs or []:
        k, sep, v = p.partition("=")
        if not sep:
            raise InputError(f"--var {k!r}: usa nombre=valor")
        out[k.strip()] = v
    for k in out:
        if not _VAR.match(k):
            raise InputError(f"variable {k!r}: minúsculas, dígitos y `_`")
        if k in BUILTIN["afectado"] | BUILTIN["vendor"]:
            raise Refused(f"{{{{{k}}}}} lo pone el script; no se da como variable")
    return out


def check_out(out: str, vault: Path, datasets: list[dict]) -> tuple[Path, bool]:
    if not out or not Path(out).is_absolute():
        raise Refused("--out debe ser una ruta absoluta, fuera del vault")
    p = _resolve(out)
    if inside(p, _resolve(vault)) or inside(_resolve(vault), p):
        raise Refused("--out no puede estar dentro del vault (ni contenerlo): los borradores nombran a terceros")
    tree = git_tree(p if p.exists() else next((a for a in p.parents if a.exists()), p))
    if tree is not None:
        raise Refused("--out está dentro de un árbol de trabajo git: un commit podría publicar los borradores")
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise Refused("--out existe y no es una carpeta vacía")
    registered = any(d.get("sensitive") not in (None, False) and isinstance(d.get("path"), str)
                     and Path(d["path"]).is_absolute() and inside(p, _resolve(d["path"])) for d in datasets)
    return p, registered


def read_avisos(pdir: Path) -> list:
    reg = pdir / "Avisos" / "registro.json"
    if not reg.is_file():
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused("Avisos/registro.json ilegible: corrígelo desde el panel Avisos") from None
    if not isinstance(data, list):
        raise Refused("Avisos/registro.json no es una lista de avisos")
    return data


def next_number(entries: list) -> int:
    nums = [int(m.group(1)) for e in entries if isinstance(e, dict)
            for m in [_AV_ID.match(str(e.get("id") or ""))] if m]
    return max(nums, default=0) + 1


def write_avisos(pdir: Path, entries: list) -> None:
    reg = pdir / "Avisos" / "registro.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg.with_name(f".registro.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, reg)


def entry(aid: str, destinatario: str, canal: str, until: date) -> dict:
    return {"id": aid, "destinatario": destinatario, "canal": canal, "enviado": None,
            "embargo_hasta": until.isoformat(), "respuesta": "", "estado": "pendiente"}


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #

def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "si", "sí", "1"))


def _files(v) -> list[str]:
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def read_findings(path: Path) -> tuple[dict[str, list[dict]], int, int]:
    """(findings by repo in first-seen order, total valid findings, invalid lines)."""
    groups: dict[str, list[dict]] = {}
    valid = invalid = 0
    try:
        fh = path.open(encoding="utf-8-sig")
    except OSError:
        raise Refused("el dataset de hallazgos no se puede abrir") from None
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                f = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            repo = f.get("repo") if isinstance(f, dict) else None
            if not isinstance(repo, str) or not repo.strip():
                invalid += 1
                continue
            groups.setdefault(repo.strip(), []).append(f)
            valid += 1
    return groups, valid, invalid


def owner_repo(repo: str) -> str | None:
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:github\.com/)?([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$", repo)
    return m.group(1) if m else None


def channel(findings: list[dict], repo: str) -> tuple[str, str, list[str]]:
    """(registry `canal`, kind, detail lines for the draft)."""
    pvr = any(_truthy(f.get("private_vulnerability_reporting")) for f in findings)
    policy = next((str(f["security_policy_url"]).strip() for f in findings
                   if isinstance(f.get("security_policy_url"), str) and f["security_policy_url"].strip()), "")
    email = next((str(f["security_email"]).strip() for f in findings
                  if isinstance(f.get("security_email"), str) and _EMAIL.fullmatch(f["security_email"].strip())), "")
    detail = []
    if policy:
        detail.append(f"- Política de seguridad publicada: {policy} — si pide otro canal, usa el suyo.")
    if email:
        detail.append(f"- Contacto de seguridad publicado: {email}")
    if pvr:
        orp = owner_repo(repo)
        form = f"https://github.com/{orp}/security/advisories/new" if orp else "(pestaña Security del repositorio)"
        return "github_pvr", "github_pvr", [f"- Informe privado de vulnerabilidad de GitHub: {form}"] + detail
    if policy:
        return "politica_seguridad", "politica_seguridad", detail
    if email:
        return f"correo: {email}", "correo", detail
    return "issue_sin_detalles", "issue_sin_detalles", [
        "- Sin canal privado publicado: abre un issue SOLO con el texto de «Primer contacto» "
        "(no dice qué se encontró ni dónde) y manda el mensaje cuando te den un contacto privado."]


def finding_values(repo: str, findings: list[dict]) -> dict[str, str]:
    files = list(dict.fromkeys(x for f in findings for x in _files(f.get("files"))))
    kinds = Counter(str(f.get("kind") or "sin tipo").strip() for f in findings)
    per = []
    for f in findings:
        fs = ", ".join(_files(f.get("files"))) or "(sin ruta)"
        per.append(f"- {fs}: {str(f.get('kind') or 'sin tipo').strip()}")
    return {"repo": repo, "files": "\n".join(f"- {x}" for x in files) or "- (sin ruta)",
            "hallazgo": "\n".join(f"- {k} ({n})" for k, n in kinds.most_common()),
            "hallazgos": "\n".join(per), "n_hallazgos": str(len(findings))}


# --------------------------------------------------------------------------- #
# Drafts
# --------------------------------------------------------------------------- #

def draft_text(aid: str, destinatario: str, canal: str, detail: list[str], until: date, tpl_name: str,
               today: date, asunto: str, mensaje: str, first_contact: str | None) -> str:
    lines = ["---", f"aviso: {aid}", f"destinatario: {json.dumps(destinatario, ensure_ascii=False)}",
             f"canal: {json.dumps(canal, ensure_ascii=False)}", f"embargo_hasta: {until.isoformat()}",
             f"plantilla: {tpl_name}", f"generado: {today.isoformat()}", "estado: pendiente", "---", "",
             f"# {aid} — {destinatario}", "",
             "> Borrador. Kairo no envía nada: léelo, mándalo tú por el canal indicado y marca el aviso "
             "como `enviado` en el panel Avisos con la fecha real (ajusta allí `embargo_hasta` si lo "
             "mandas más tarde).", "", "## Canal sugerido", "", f"`{canal}`", ""]
    lines += detail + [""]
    if first_contact is not None:
        lines += ["## Primer contacto (issue público, sin detalles)", "", first_contact.strip(), ""]
        lines += ["## Mensaje (solo por el canal privado que te den; NO en el issue)", ""]
    lines += ["## Asunto", "", asunto.strip() or f"({aid})", "", "## Mensaje", "", mensaje.strip(), ""]
    return "\n".join(lines)


def _missing(texts: list[str]) -> list[str]:
    return sorted({n for t in texts for n in _PH.findall(t)})


def _file_name(aid: str, destinatario: str) -> str:
    return f"{aid}-{_SLUG.sub('-', destinatario).strip('-')[:60] or 'aviso'}.md"


def _write_drafts(out: Path, drafts: list[tuple[str, str]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, text in drafts:
        (out / name).write_text(text, encoding="utf-8")


def _refuse_secrets(out: Path, drafts: list[tuple[str, str, str]]) -> None:
    """drafts: (file name, text, destinatario). Writes DIR/_problemas.txt and refuses."""
    hit = [(dest, redact.redact_text(text)[1]) for _name, text, dest in drafts if redact.has_secret(text)]
    if not hit:
        return
    out.mkdir(parents=True, exist_ok=True)
    body = ["Borradores con material privado (no se escribió ninguno):", ""]
    body += [f"- {dest}: " + ", ".join(f"{f['type']} sha256:{f['sha256']}" for f in fs) for dest, fs in hit]
    body += ["", "El dataset de hallazgos debe llevar huellas, nunca valores: pásalo por redact.py y repite."]
    (out / "_problemas.txt").write_text("\n".join(body) + "\n", encoding="utf-8")
    raise Refused(f"{len(hit)} borrador(es) llevarían material privado; no se escribió nada "
                  "(los repositorios están en _problemas.txt, dentro de --out)")


def cmd_template(a) -> tuple[int, dict]:
    pdir = project_dir(Path(a.vault), a.project)
    path = template_path(pdir, a.name)
    created = False
    if a.new:
        if path.exists():
            raise Refused(f"ya existe {TEMPLATES}/{path.name}; edítala o usa otro nombre")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SKELETON[a.kind], encoding="utf-8")
        created = True
    res = check_template(load_template(path))
    return (0 if res["ok"] else 3), {"tool": TOOL, "plantilla": f"Projects/{pdir.name}/{TEMPLATES}/{path.name}",
                                     "creada": created, **res}


def _common_setup(a, environ: Mapping[str, str], kind: str):
    m = agent_marker(environ)
    if m:
        raise Refused(f"los borradores de avisos son del investigador: rechazado dentro de una sesión de agente "
                      f"({m}); escribe en Avisos/, que ningún modelo lee")
    if a.embargo_days < 1 or a.embargo_days > 3650:
        raise InputError("--embargo-days: entre 1 y 3650")
    vault = Path(a.vault)
    pdir = project_dir(vault, a.project)
    datasets = load_datasets(pdir)
    out, registered = check_out(a.out, vault, datasets)
    tpl = load_template(template_path(pdir, a.template))
    chk = check_template(tpl)
    if not chk["ok"]:
        raise Refused("la plantilla no pasa la comprobación (notices.py template --name "
                      f"{a.template}): " + "; ".join(chk["problemas"]))
    if chk["kind"] != kind:
        raise Refused(f"la plantilla es de tipo `{chk['kind']}`; esta orden usa plantillas `{kind}`")
    variables = parse_vars(a.var, a.vars_file, kind)
    return vault, pdir, datasets, out, registered, tpl, variables


def cmd_draft(a, environ: Mapping[str, str], today: date) -> tuple[int, dict]:
    vault, pdir, datasets, out, registered, tpl, variables = _common_setup(a, environ, "afectado")
    ds = [d for d in datasets if str(d.get("name") or "") == a.dataset]
    if len(ds) != 1:
        raise InputError(f"no hay exactamente un dataset llamado {a.dataset!r} en Datos/registro.json")
    raw = ds[0].get("path")
    if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).is_file():
        raise Refused("el dataset de hallazgos debe ser un fichero JSONL (kind file) con ruta absoluta que exista")
    groups, n_valid, n_invalid = read_findings(Path(raw))
    avisos = read_avisos(pdir)
    open_dest = {str(e.get("destinatario")) for e in avisos
                 if isinstance(e, dict) and e.get("estado") in OPEN_STATES}
    until = today + timedelta(days=a.embargo_days)
    n = next_number(avisos)
    drafts, new_entries, canales, skipped = [], [], Counter(), 0
    missing: set[str] = set()
    for repo, findings in groups.items():
        if repo in open_dest:
            skipped += 1
            continue
        aid = f"AV-{n:03d}"
        n += 1
        canal, ckind, detail = channel(findings, repo)
        values = {**variables, **finding_values(repo, findings), "id_aviso": aid, "canal": canal,
                  "fecha": today.isoformat(), "fecha_limite": until.isoformat(), "embargo_dias": str(a.embargo_days)}
        asunto, mensaje = render(tpl["asunto"], values), render(tpl["mensaje"], values)
        first = None
        if ckind == "issue_sin_detalles":
            first = render(tpl["primer_contacto"] or DEFAULT_FIRST_CONTACT, values)
        missing |= set(_missing([asunto, mensaje, first or ""]))
        drafts.append((_file_name(aid, repo), draft_text(aid, repo, canal, detail, until, a.template, today,
                                                         asunto, mensaje, first), repo))
        new_entries.append(entry(aid, repo, canal, until))
        canales[ckind] += 1
    if missing:
        raise Refused("marcadores sin valor: " + ", ".join("{{" + m + "}}" for m in sorted(missing))
                      + " (dalos con --var nombre=valor o --vars-file)")
    _refuse_secrets(out, drafts)
    _write_drafts(out, [(name, text) for name, text, _ in drafts])
    if new_entries:
        write_avisos(pdir, avisos + new_entries)
    ids = [e["id"] for e in new_entries]
    return 0, {"tool": TOOL, "borradores": len(drafts), "repositorios": len(groups), "hallazgos": n_valid,
               "lineas_invalidas": n_invalid, "ya_abiertos": skipped, "canales": dict(canales),
               "avisos": f"{ids[0]}..{ids[-1]}" if len(ids) > 1 else (ids[0] if ids else ""),
               "embargo_hasta": until.isoformat(), "out_registrado_sensible": registered,
               "registro": f"Projects/{pdir.name}/Avisos/registro.json",
               "siguiente": ("Lee cada borrador y mándalo tú por su canal; márcalo `enviado` en el panel Avisos. "
                             + ("" if registered else "Registra la carpeta --out como dataset sensible (panel Datos) "
                                "para que el guardia de datos la proteja."))}


def cmd_vendor(a, environ: Mapping[str, str], today: date) -> tuple[int, dict]:
    vault, pdir, datasets, out, registered, tpl, variables = _common_setup(a, environ, "vendor")
    dest, canal = " ".join(a.destinatario.split()), " ".join(a.canal.split())
    if not dest or not canal:
        raise InputError("--destinatario y --canal no pueden estar vacíos")
    avisos = read_avisos(pdir)
    if any(isinstance(e, dict) and str(e.get("destinatario")) == dest and e.get("estado") in OPEN_STATES
           for e in avisos):
        raise Refused("ya hay un aviso abierto (pendiente o enviado) para ese destinatario")
    until = today + timedelta(days=a.embargo_days)
    aid = f"AV-{next_number(avisos):03d}"
    values = {**variables, "destinatario": dest, "id_aviso": aid, "canal": canal, "fecha": today.isoformat(),
              "fecha_limite": until.isoformat(), "embargo_dias": str(a.embargo_days)}
    asunto, mensaje = render(tpl["asunto"], values), render(tpl["mensaje"], values)
    missing = _missing([asunto, mensaje])
    if missing:
        raise Refused("marcadores sin valor: " + ", ".join("{{" + m + "}}" for m in missing)
                      + " (dalos con --var nombre=valor o --vars-file)")
    drafts = [(_file_name(aid, dest), draft_text(aid, dest, canal, [], until, a.template, today, asunto,
                                                 mensaje, None), dest)]
    _refuse_secrets(out, drafts)
    _write_drafts(out, [(name, text) for name, text, _ in drafts])
    write_avisos(pdir, avisos + [entry(aid, dest, canal, until)])
    return 0, {"tool": TOOL, "borradores": 1, "avisos": aid, "embargo_hasta": until.isoformat(),
               "out_registrado_sensible": registered, "registro": f"Projects/{pdir.name}/Avisos/registro.json",
               "siguiente": "Lee el borrador y mándalo tú; márcalo `enviado` en el panel Avisos."}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="notices.py", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    t.add_argument("--vault", required=True)
    t.add_argument("--project", required=True)
    t.add_argument("--name", required=True)
    t.add_argument("--kind", choices=KINDS, default="afectado")
    t.add_argument("--new", action="store_true")
    for name in ("draft", "vendor"):
        s = sub.add_parser(name)
        s.add_argument("--vault", required=True)
        s.add_argument("--project", required=True)
        s.add_argument("--template", required=True)
        s.add_argument("--out", required=True)
        s.add_argument("--embargo-days", type=int, default=DEFAULT_EMBARGO)
        s.add_argument("--var", action="append", default=[])
        s.add_argument("--vars-file")
        if name == "draft":
            s.add_argument("--dataset", required=True)
        else:
            s.add_argument("--destinatario", required=True)
            s.add_argument("--canal", required=True)
    return ap


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None,
         today: date | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    environ = os.environ if environ is None else environ
    today = today or date.today()
    try:
        if a.cmd == "template":
            code, res = cmd_template(a)
        elif a.cmd == "draft":
            code, res = cmd_draft(a, environ, today)
        else:
            code, res = cmd_vendor(a, environ, today)
    except Refused as exc:
        print(json.dumps({"tool": TOOL, "refused": True, "error": str(exc)}, ensure_ascii=False))
        return 3
    except InputError as exc:
        print(f"notices: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
