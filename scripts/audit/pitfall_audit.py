#!/usr/bin/env python3
"""Pitfall audit — run by update-confidence before any evidence-based status move.

Four checks per adjudicating experiment, after the pitfall taxonomy of Luo,
Kasirzadeh & Shah, "The More You Automate, the Less You See: Hidden Pitfalls of
AI Scientist Systems" (NeurIPS 2025 AI4Science; arXiv:2509.08713):

  a) benchmark  — inappropriate benchmark selection: is the task / data / setup of
                  the frozen design the one the hypothesis claim is worded about?
  b) leakage    — data leakage: train/val/test overlap, the recorded leakage sanity
                  check, and information from the future (runs before the freeze,
                  thresholds / stop points / r* not in the frozen text or fit on
                  held-out data, analysis amendments written after the runs).
  c) metric     — metric misuse: is the metric behind the result exactly the frozen
                  primary metric (same cell, same estimator, same thresholds, the
                  analysis script the frozen `analysis_plan` names)?
  d) selection  — post-hoc selection bias: do the runs in the analysis match the
                  trace index (scripts/traces/trace_index.py)? A run, seed or
                  condition executed but left out without a preregistered reason
                  is crítico.

The mechanical parts are here. The judgement part of (a) — is this benchmark the
right test of the claim as worded? — is written by the pitfall-audit skill as a
`--judgement` file with a severity; when it is missing the report says so
(`importante`), it is never passed silently.

    pitfall_audit.py audit --experiment <E-XXXX.md> --hypothesis <H-XXXX.md>
        [--trace <index.jsonl>]           default <Experimentos>/trazas/index.jsonl
        [--analysis <E-XXXX.analysis.json>] default <Experimentos>/trazas/<E-XXXX>.analysis.json
        [--plan <E-XXXX.data.json>]       default <Experimentos>/<E-XXXX>.data.json if present
        [--claim-setup <json>] [--design-setup <json>] [--splits <json>]
        [--judgement <json>] [--apply] [--json] [--out <report.json>]
    pitfall_audit.py flag-review --hypothesis <H-XXXX.md>

Default is a dry run: nothing is written. With `--apply`, a crítico finding sets
`needs_human_review: true` on the hypothesis note — that single field, through
`set_needs_human_review`, never `status`. The audit never moves status.

Exit codes: 0 no crítico · 3 at least one crítico (the transition is blocked) ·
1 error (an input could not be read).

Input formats are documented in skills/pitfall-audit/SKILL.md. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "traces"))
from notes import read_note, section  # noqa: E402
import trace_index  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/pitfall_audit@{__version__}"
SEVERITIES = ("crítico", "importante", "menor")
CHECKS = ("benchmark", "leakage", "metric", "selection")
FROZEN_SECTIONS = ("Predicción", "Variables", "Diseño", "Plan de análisis", "Umbral de invalidez")
ESTIMATORS = {
    "median": ("median", "mediana"),
    "mean": ("mean", "media", "promedio", "average"),
    "trimmed_mean": ("trimmed mean", "media recortada"),
    "max": ("maximum", "máximo", "best"),
}
ANALYSIS_SCRIPTS = {"frequentist": "two_proportion_test", "bayesian": "bayes_factor_proportions"}


class AuditError(Exception):
    """An input could not be read (exit 1)."""


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------

def _load_json(path: Path | None, what: str):
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read {what} {path}: {exc}") from exc


def _note(path: Path, what: str) -> tuple[dict, str]:
    parsed = read_note(path)
    if parsed is None:
        raise AuditError(f"cannot read {what} {path}")
    return parsed


def nested_scalars(path: Path, key: str) -> dict[str, str]:
    """`key:` followed by indented `sub: value  # comment` lines -> {sub: value}."""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = text.split("\n")
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:\s*(#.*)?$", line):
            for sub in lines[i + 1:]:
                if not sub.startswith((" ", "\t")):
                    break
                m = re.match(r"^\s+([A-Za-z_]\w*)\s*:\s*(.*)$", sub)
                if m:
                    val = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip("'\"")
                    out[m.group(1)] = val
            break
    return out


_FRACTIONS = {"½": " 0.5", "⅓": " 0.333333333", "⅔": " 0.666666667", "¼": " 0.25", "¾": " 0.75"}


_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")


def _sci(m: re.Match) -> str:
    exp = m.group(2).translate(_SUP).lstrip("^")
    return f"{m.group(0)} {float(m.group(1)) * 10 ** int(exp):g}"


def _norm_num_text(text: str) -> str:
    # "10 000" / "10 000" -> "10000"; "5×10³" / "1.0x10^5" also read as 5000 / 100000;
    # "⅓" -> 0.333333333; decimal commas are not used in notes
    text = re.sub(r"\b[A-Z]+-\d+\b", " ", text)          # note ids (E-0001, P-0002) are not values
    text = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[×x·]\s*10(\^-?\d+|[⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)", _sci, text)
    for k, v in _FRACTIONS.items():
        text = text.replace(k, v)
    return text


def numbers_in(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", _norm_num_text(text))]


def has_number(text: str, value: float) -> bool:
    return any(abs(x - float(value)) < 1e-6 for x in numbers_in(text))


def has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE) is not None


def parse_time(v) -> datetime | None:
    if not isinstance(v, str) or v == "unknown":
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    return None


def amendments(body: str) -> list[dict]:
    """`## Enmiendas` entries: `### YYYY-MM-DD — title` + text."""
    text = section(body, "Enmiendas")
    out = []
    for m in re.finditer(r"^###\s+(\d{4}-\d{2}-\d{2})\s*[—-]*\s*(.*?)$(.*?)(?=^###\s|\Z)", text,
                         re.MULTILINE | re.DOTALL):
        out.append({"date": m.group(1), "title": m.group(2).strip(), "text": m.group(3)})
    return out


def _val(x):
    """Setup facets may be `value` or {"value": ..., "source": ..., "severity": ...}."""
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def _src(x) -> str | None:
    return x.get("source") if isinstance(x, dict) else None


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

class Check:
    def __init__(self, name: str):
        self.name = name
        self.findings: list[dict] = []
        self.passed: list[str] = []

    def find(self, severity: str, location: str, message: str, kind: str, by: str = TOOL):
        assert severity in SEVERITIES, severity
        self.findings.append({"severity": severity, "location": location, "message": message,
                              "kind": kind, "by": by})

    def ok(self, what: str):
        self.passed.append(what)

    def as_dict(self) -> dict:
        worst = next((s for s in SEVERITIES if any(f["severity"] == s for f in self.findings)), None)
        return {"status": "pass" if not self.findings else "finding", "worst": worst,
                "findings": self.findings, "passed": self.passed}


# ---------------------------------------------------------------------------
# (a) benchmark
# ---------------------------------------------------------------------------

def _compare_facet(claim, design) -> tuple[bool | None, str]:
    """(match, expectation). None = cannot compare mechanically."""
    c, d = _val(claim), _val(design)
    if isinstance(c, str) and c.lower() == "any":
        return True, "any"
    if isinstance(c, dict):
        if "min" in c:
            if isinstance(d, (int, float)):
                return d >= c["min"], f">= {c['min']}"
            return None, f">= {c['min']}"
        if "max" in c:
            if isinstance(d, (int, float)):
                return d <= c["max"], f"<= {c['max']}"
            return None, f"<= {c['max']}"
        if "any_of" in c:
            allowed = [str(x).lower() for x in c["any_of"]]
            ds = d if isinstance(d, list) else [d]
            return all(str(x).lower() in allowed for x in ds), f"one of {c['any_of']}"
        if "includes" in c:
            ds = [str(x).lower() for x in (d if isinstance(d, list) else [d])]
            return all(str(x).lower() in ds for x in c["includes"]), f"includes {c['includes']}"
        return None, json.dumps(c, ensure_ascii=False)
    if isinstance(c, (int, float)) and isinstance(d, (int, float)):
        return abs(c - d) < 1e-9, str(c)
    if isinstance(c, list):
        ds = d if isinstance(d, list) else [d]
        return sorted(map(str, c)) == sorted(map(str, ds)), str(c)
    return str(c).strip().lower() == str(d).strip().lower(), str(c)


def check_benchmark(chk: Check, eid: str, hid: str, claim_setup, design_setup, plan) -> None:
    design = dict(design_setup or {})
    task = (plan or {}).get("task") if isinstance(plan, dict) else None
    if isinstance(task, dict):
        auto = {}
        if "name" in task:
            auto["task"] = task["name"]
        for k in ("modulus_P", "modulus", "P"):
            if k in task:
                auto["modulus"] = task[k]
                break
        for k, v in auto.items():
            if k in design and _compare_facet(design[k], v)[0] is False:
                chk.find("importante", f"{eid}.data.json task.{k}",
                         f"the design-setup file says {k} = {_val(design[k])!r} but the frozen data "
                         f"manifest says {v!r}; the manifest is used", "design_setup_conflict")
            design[k] = {"value": v, "source": f"{eid}.data.json task"}
    if not claim_setup:
        chk.find("importante", f"{hid} ## Claim",
                 "no machine-readable claim setup was given: the claim's stated task / data / model "
                 "could not be compared mechanically with the frozen design", "claim_setup_missing")
        return
    for facet, cval in claim_setup.items():
        if facet.startswith("_"):
            continue
        sev = cval.get("severity") if isinstance(cval, dict) and cval.get("severity") in SEVERITIES \
            else ("crítico" if facet == "task" else "importante")
        csrc = _src(cval) or f"{hid} ## Claim"
        if facet not in design:
            chk.find("importante", csrc,
                     f"the claim fixes `{facet}` = {json.dumps(_val(cval), ensure_ascii=False)} but "
                     f"the frozen design of {eid} does not state it", "facet_not_in_design")
            continue
        match, expect = _compare_facet(cval, design[facet])
        dsrc = _src(design[facet]) or eid
        if match is True:
            chk.ok(f"{facet}: {json.dumps(_val(design[facet]), ensure_ascii=False)} ({expect})")
        elif match is False:
            chk.find(sev, dsrc,
                     f"`{facet}`: the claim ({csrc}) requires {expect}; the frozen design has "
                     f"{json.dumps(_val(design[facet]), ensure_ascii=False)}", "setup_mismatch")
        else:
            chk.find("importante", dsrc,
                     f"`{facet}`: the claim requires {expect}; the design value "
                     f"{json.dumps(_val(design[facet]), ensure_ascii=False)} cannot be compared "
                     "mechanically — judgement needed", "facet_not_comparable")


# ---------------------------------------------------------------------------
# (b) leakage
# ---------------------------------------------------------------------------

def check_leakage(chk: Check, eid: str, fm: dict, exp_path: Path, frozen_text: str, body: str,
                  runs: list[dict], analysis, splits) -> None:
    sanity = nested_scalars(exp_path, "sanity_checks")
    leak = sanity.get("no_data_leakage", "").lower()
    if leak == "false":
        chk.find("crítico", f"{eid} sanity_checks.no_data_leakage",
                 "the run's own leakage check failed (no_data_leakage: false)", "leakage_check_failed")
    elif leak == "true":
        chk.ok("sanity_checks.no_data_leakage: true (run-experiment step 3)")
    else:
        chk.find("importante", f"{eid} sanity_checks.no_data_leakage",
                 "the leakage sanity check has no recorded result", "leakage_check_missing")

    # train / val / test overlap, when the split ids are available
    if splits:
        per_run = splits.get("runs", splits)
        n_checked = 0
        for rid, parts in per_run.items():
            if not isinstance(parts, dict):
                continue
            names = [k for k in ("train", "val", "test") if isinstance(parts.get(k), list)]
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    inter = set(map(str, parts[a])) & set(map(str, parts[b]))
                    if inter:
                        chk.find("crítico", f"splits run {rid}",
                                 f"{a}/{b} overlap: {len(inter)} shared id(s), e.g. "
                                 f"{sorted(inter)[:3]}", "split_overlap")
            n_checked += 1
        if n_checked and not any(f["kind"] == "split_overlap" for f in chk.findings):
            chk.ok(f"no train/val/test overlap in the split ids of {n_checked} run(s)")
    else:
        chk.find("menor", f"{eid} ## Umbral de invalidez",
                 "split ids were not given to the audit: overlap is not re-verified here, only the "
                 "recorded sanity check is read", "overlap_not_reverified")

    # information from the future (1): a run that started before the freeze
    frozen = parse_time(str(fm.get("frozen_at", "")))
    if frozen is None:
        chk.find("crítico", f"{eid} frozen_at",
                 "no frozen_at: the audit cannot show the design was fixed before the runs",
                 "no_freeze_time")
    else:
        early, unordered, unknown = [], [], []
        for r in runs:
            if r.get("outcome") == "not_launched":
                continue
            st = parse_time(r.get("started_at"))
            after = parse_time(r.get("started_after"))
            day = parse_time(r.get("date"))
            if st is not None:
                (early if st < frozen else []).append(r["run_id"])
            elif after is not None:
                if after < frozen:
                    unordered.append(r["run_id"])
            elif day is not None:
                if day.date() < frozen.date():
                    early.append(r["run_id"])
                elif day.date() == frozen.date():
                    unordered.append(r["run_id"])
            else:
                unknown.append(r["run_id"])
        if early:
            chk.find("crítico", "trazas/index.jsonl",
                     f"run(s) {early} started before frozen_at {fm.get('frozen_at')}: data was seen "
                     "before the design was fixed", "run_before_freeze")
        if unordered:
            chk.find("importante", "trazas/index.jsonl",
                     f"run(s) {unordered}: start time not recorded precisely enough to show it came "
                     f"after frozen_at {fm.get('frozen_at')}", "run_freeze_order_unknown")
        if unknown:
            chk.find("importante", "trazas/index.jsonl",
                     f"run(s) {unknown}: start time unknown — order against the freeze unverifiable",
                     "run_time_unknown")
        if runs and not (early or unordered or unknown):
            chk.ok("every launched run started after frozen_at")

    # information from the future (2): analysis parameters not frozen, or fit on held-out data
    params = (analysis or {}).get("parameters_used") or {}
    for name, spec in params.items():
        value = spec.get("value") if isinstance(spec, dict) else spec
        chosen_on = (spec.get("chosen_on") if isinstance(spec, dict) else None) or "frozen"
        in_text = isinstance(value, (int, float)) and has_number(frozen_text, value)
        if chosen_on in ("test", "validation", "val", "held_out"):
            chosen_at = parse_time(spec.get("chosen_at")) if isinstance(spec, dict) else None
            if chosen_at is None or frozen is None or chosen_at >= frozen or not in_text:
                chk.find("crítico", f"analysis parameter `{name}`",
                         f"`{name}` = {value} was chosen on {chosen_on} data "
                         f"{'after the freeze' if chosen_at and frozen and chosen_at >= frozen else '(time not shown to precede the freeze)'}"
                         " — information from the future", "param_fit_on_heldout")
                continue
        if isinstance(value, (int, float)):
            if in_text:
                chk.ok(f"parameter `{name}` = {value} appears in the frozen sections")
            else:
                chk.find("crítico", f"analysis parameter `{name}`",
                         f"`{name}` = {value} is used by the analysis but appears nowhere in the frozen "
                         f"sections ({', '.join(FROZEN_SECTIONS)}) — set after the fact?",
                         "param_not_frozen")
        else:
            chk.find("importante", f"analysis parameter `{name}`",
                     f"non-numeric parameter {value!r}: not verifiable against the frozen text",
                     "param_not_comparable")

    # information from the future (3): analysis amendments written after the runs started
    # the run day: exact start, else the recorded date, else the lower bound
    starts = [parse_time(r.get("started_at")) or parse_time(r.get("date")) or parse_time(r.get("started_after"))
              for r in runs if r.get("outcome") != "not_launched"]
    starts = [s for s in starts if s is not None]
    if starts:
        first = min(starts).date()
        for am in amendments(body):
            d = datetime.strptime(am["date"], "%Y-%m-%d").date()
            touches = re.search(r"an[aá]lisis|analysis|umbral|threshold|m[ée]trica|metric|parada|stop",
                                am["title"] + am["text"], re.IGNORECASE)
            if not touches:
                continue
            if d > first:
                chk.find("importante", f"{eid} ## Enmiendas {am['date']} ({am['title'][:60]})",
                         "an amendment that touches the analysis is dated after the first run started",
                         "amendment_after_runs")
            elif d == first:
                chk.find("menor", f"{eid} ## Enmiendas {am['date']} ({am['title'][:60]})",
                         "an amendment that touches the analysis has the same date as the first run; "
                         "dates alone cannot order them", "amendment_same_day")


# ---------------------------------------------------------------------------
# (c) metric
# ---------------------------------------------------------------------------

def _primary_and_secondary_text(body: str) -> tuple[str, str]:
    plan = section(body, "Plan de análisis")
    variables = section(body, "Variables")
    primary, secondary = [], []
    for para in re.split(r"\n\s*\n", plan):
        if re.search(r"m[ée]trica primaria|primary metric", para, re.IGNORECASE):
            primary.append(para)
    for block in re.split(r"\n(?=\s*[-*] )", variables):
        low = block.lower()
        if re.search(r"primari[ao]s?\b|primary", low) and "secundari" not in low[:40]:
            primary.append(block)
        elif re.search(r"secundari|secondary|exploratori", low):
            secondary.append(block)
    return "\n".join(primary), "\n".join(secondary)


def check_metric(chk: Check, eid: str, fm: dict, body: str, analysis, live: bool = False) -> None:
    if not analysis:
        chk.find("crítico", f"trazas/{eid}.analysis.json",
                 "no analysis record: the audit cannot show the result uses the preregistered primary "
                 "metric", "analysis_record_missing")
        return
    if analysis.get("reconstructed"):
        if live:
            chk.find("crítico", f"trazas/{eid}.analysis.json",
                     "the analysis record was reconstructed after the fact: a live transition needs the "
                     "record run-experiment wrote from the analysis output", "analysis_reconstructed")
        else:
            chk.find("menor", f"trazas/{eid}.analysis.json",
                     "analysis record reconstructed after the fact (dry run): it is only as exact as "
                     f"its source ({analysis.get('source', 'unstated')})", "analysis_reconstructed")
    primary_text, secondary_text = _primary_and_secondary_text(body)
    plan_text = section(body, "Plan de análisis")
    if not primary_text:
        chk.find("importante", f"{eid} ## Plan de análisis",
                 "the frozen plan names no primary metric the audit can find", "no_frozen_primary")
    pm = analysis.get("primary_metric") or {}
    rm = analysis.get("reported_metric") or pm
    if not pm:
        chk.find("crítico", f"trazas/{eid}.analysis.json",
                 "the analysis record does not state which metric it computed as primary",
                 "primary_metric_missing")
        return
    for key in ("name", "cell", "estimator"):
        if rm.get(key) != pm.get(key):
            chk.find("crítico", f"trazas/{eid}.analysis.json reported_metric.{key}",
                     f"the result is based on {key} = {rm.get(key)!r}, not the primary "
                     f"{pm.get(key)!r}", "reported_not_primary")
    loc = f"{eid} ## Variables / ## Plan de análisis (primary metric)"
    for k, v in (rm.get("cell") or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            in_p, in_s = has_number(primary_text, v), has_number(secondary_text, v)
        else:
            in_p, in_s = has_word(primary_text, str(v)), has_word(secondary_text, str(v))
        if in_p:
            chk.ok(f"cell {k} = {v} is the frozen primary cell")
        elif in_s:
            chk.find("crítico", loc,
                     f"cell {k} = {v} belongs to a frozen SECONDARY metric: a secondary result is "
                     "reported as primary", "secondary_as_primary")
        elif isinstance(v, (int, float)):
            chk.find("crítico", loc, f"cell {k} = {v} is not in the frozen primary metric", "cell_not_frozen")
        else:
            chk.find("importante", loc,
                     f"cell {k} = {v!r} not found verbatim in the frozen primary metric (naming?) — "
                     "judgement needed", "cell_label_unverified")
    est = str(rm.get("estimator") or "").lower().replace(" ", "_")
    if est:
        mine = ESTIMATORS.get(est, (est.replace("_", " "),))
        others = [w for k, ws in ESTIMATORS.items() if k != est for w in ws if w not in mine]
        if any(has_word(primary_text, w) for w in mine):
            chk.ok(f"estimator {est} matches the frozen primary metric")
        elif any(has_word(primary_text, w) for w in others):
            chk.find("crítico", loc, f"estimator {est!r} differs from the frozen one", "estimator_mismatch")
        else:
            chk.find("importante", loc, f"estimator {est!r} not named in the frozen primary metric",
                     "estimator_unverified")
    for group in ("thresholds", "interval"):
        for k, v in (rm.get(group) or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if has_number(plan_text, v) or has_number(primary_text, v):
                chk.ok(f"{group}.{k} = {v} as frozen")
            else:
                chk.find("crítico", f"{eid} ## Plan de análisis",
                         f"{group}.{k} = {v} is not the frozen value", f"{group}_not_frozen")
    plan_kind = str(fm.get("analysis_plan", "")).strip()
    script = str(analysis.get("script") or "")
    if plan_kind in ANALYSIS_SCRIPTS:
        if ANALYSIS_SCRIPTS[plan_kind] in script:
            chk.ok(f"script {script} matches analysis_plan: {plan_kind}")
        else:
            chk.find("crítico", f"{eid} analysis_plan",
                     f"analysis_plan is {plan_kind} ({ANALYSIS_SCRIPTS[plan_kind]}.py) but the result "
                     f"came from {script or 'an unnamed script'}", "wrong_analysis_script")


# ---------------------------------------------------------------------------
# (d) selection
# ---------------------------------------------------------------------------

def _source_problem(src: str, body: str, run: dict) -> tuple[str, str] | None:
    """(severity, message) when an exclusion's cited rule does not hold up, else None."""
    heads = re.findall(r"##\s*([^#(\[;,]+?)(?:\s*[\[(;,]|$)", src)
    if not heads:
        return ("menor", f"exclusion rule cited outside the experiment note ({src}); not verified here")
    for h in heads:
        h = h.strip()
        if not h.startswith("Enmiendas") and not section(body, h):
            return ("crítico", f"cited rule `## {h}` does not exist in the experiment note")
        if h.startswith("Enmiendas"):
            m = re.search(r"\d{4}-\d{2}-\d{2}", src)
            if not m:
                return ("importante", "exclusion rests on an undated amendment")
            run_day = parse_time(run.get("started_at")) or parse_time(run.get("date")) \
                or parse_time(run.get("started_after"))
            if run_day and datetime.strptime(m.group(0), "%Y-%m-%d").date() > run_day.date():
                return ("crítico", f"exclusion rule amendment {m.group(0)} postdates the run — "
                                   "an exclusion written after seeing the result")
            return ("menor", f"exclusion rests on a pre-run amendment ({m.group(0)}), not the frozen text")
    return None


def check_selection(chk: Check, eid: str, body: str, trace_path: Path, analysis, plan) -> list[dict]:
    if not trace_path.exists():
        chk.find("crítico", trace_path.as_posix(),
                 "no trace index: which runs were executed cannot be shown, so post-hoc selection "
                 "cannot be excluded", "trace_missing")
        return []
    problems = trace_index.chain_problems(trace_index.read_lines(trace_path))
    if problems:
        chk.find("crítico", trace_path.as_posix(),
                 "trace index fails verification (edited, deleted or reordered lines): "
                 + "; ".join(problems[:3]), "trace_broken")
        return []
    entries = [json.loads(l) for l in trace_index.read_lines(trace_path)]
    runs = [r for r in trace_index.latest_by_run(entries).values() if r["experiment"] == eid]
    if not runs:
        chk.find("crítico", trace_path.as_posix(),
                 f"no run of {eid} in the trace index", "no_runs_traced")
        return []
    chk.ok(f"trace index verifies; {len(runs)} run(s) of {eid}")
    loc = lambda r: f"trazas/index.jsonl seq {r['seq']} ({r['run_id']})"  # noqa: E731

    for r in runs:
        if r["outcome"] == "running":
            chk.find("crítico", loc(r), "run started but never closed: its outcome and whether it "
                     "entered the analysis are unrecorded", "run_not_closed")
            continue
        ea = r.get("entered_analysis")
        if ea is None:
            chk.find("importante", loc(r), "unknown whether this run entered the analysis",
                     "analysis_membership_unknown")
        elif ea is False:
            src = r.get("exclusion_rule_source")
            if not src:
                chk.find("crítico", loc(r),
                         f"{r['outcome']} run left out of the analysis without a preregistered reason "
                         f"(reason given: {r.get('exclusion_reason')!r})", "unexplained_exclusion")
            else:
                prob = _source_problem(src, body, r)
                if prob:
                    chk.find(prob[0], loc(r), f"excluded ({r.get('exclusion_reason')}): {prob[1]}",
                             "exclusion_rule_" + ("invalid" if prob[0] == "crítico" else "weak"))
        if r.get("attempt") and r["attempt"] > 1 and not r.get("retry_of"):
            chk.find("importante", loc(r), "a retry that does not name the attempt it retries",
                     "retry_unlinked")

    # completed runs whose outputs were not archived: the analysis cannot be re-derived
    unarchived = [r["run_id"] for r in runs if r["outcome"] in ("completed", "invalid")
                  and not any(a.get("archived") and a.get("sha256") for a in (r.get("artifacts") or []))]
    if unarchived:
        chk.find("importante", "trazas/index.jsonl artifacts",
                 f"{len(unarchived)} run(s) have no archived output (path + sha256): the analysis "
                 f"cannot be re-derived from the vault — {unarchived[:6]}{'…' if len(unarchived) > 6 else ''}",
                 "outputs_not_archived")

    # the frozen plan: every planned run traced, with its seed; nothing extra entered
    planned = {}
    if isinstance(plan, dict) and isinstance(plan.get("runs"), list):
        planned = {p.get("run_index"): p for p in plan["runs"] if isinstance(p, dict)}
    if planned:
        by_idx: dict = {}
        for r in runs:
            by_idx.setdefault(r.get("plan_index"), []).append(r)
        missing = sorted(i for i in planned if i not in by_idx)
        if missing:
            chk.find("crítico", f"{eid}.data.json",
                     f"planned run(s) {missing} have no trace entry: their fate is unknown",
                     "planned_run_untraced")
        for r in runs:
            idx = r.get("plan_index")
            if idx is None or idx not in planned:
                sev = "crítico" if r.get("entered_analysis") else "importante"
                chk.find(sev, loc(r), "run outside the frozen plan"
                         + (" entered the analysis (a seed added after the fact?)" if sev == "crítico" else ""),
                         "run_outside_plan")
                continue
            seed = planned[idx].get("run_seed", planned[idx].get("seed"))
            if seed is not None and r.get("outcome") != "not_launched":
                if r.get("seeds") == "unknown":
                    chk.find("importante", loc(r), "seed unknown", "seed_unknown")
                elif seed not in r["seeds"]:
                    chk.find("crítico", loc(r), f"seed {r['seeds']} differs from the frozen plan's {seed}",
                             "seed_mismatch")
        if not missing:
            chk.ok(f"all {len(planned)} planned runs are traced")

    # the analysis record against the trace
    if analysis:
        traced = {r["run_id"]: r for r in runs}
        listed = analysis.get("runs_in_analysis")
        cells = analysis.get("cells")
        if isinstance(listed, list):
            listed_set = set(listed)
            for rid in sorted(listed_set - set(traced)):
                chk.find("crítico", f"trazas/{eid}.analysis.json", f"run {rid} is in the analysis but "
                         "not in the trace index", "untraced_run_in_analysis")
            for rid, r in traced.items():
                if r.get("entered_analysis") is True and rid not in listed_set:
                    chk.find("crítico", loc(r), "trace says this run entered the analysis but the "
                             "analysis does not list it (silently dropped)", "dropped_from_analysis")
                if r.get("entered_analysis") is False and rid in listed_set:
                    chk.find("crítico", loc(r), "trace says excluded, analysis includes it",
                             "membership_conflict")
        if isinstance(cells, dict):
            for cell, info in cells.items():
                n = info.get("n_completed") if isinstance(info, dict) else info
                have = [r for r in runs if r.get("cell") == cell and r.get("entered_analysis") is True
                        and r["outcome"] == "completed"]
                if isinstance(n, int) and n != len(have):
                    chk.find("crítico", f"trazas/{eid}.analysis.json cells.{cell}",
                             f"analysis counts {n} completed run(s) in {cell}; the trace has {len(have)} "
                             "completed runs that entered the analysis", "cell_count_mismatch")
                elif isinstance(n, int):
                    chk.ok(f"cell {cell}: {n} completed run(s), as traced")
            for r in runs:
                if r.get("entered_analysis") is True and r["outcome"] == "completed" \
                        and r.get("cell") not in cells:
                    chk.find("crítico", loc(r), f"traced cell {r.get('cell')!r} is missing from the analysis",
                             "cell_missing_from_analysis")
        if not isinstance(listed, list) and not isinstance(cells, dict):
            chk.find("importante", f"trazas/{eid}.analysis.json",
                     "the analysis record lists neither runs nor cells: only exclusion reasons were "
                     "checked", "analysis_membership_unlisted")

    # preregistered conditions that ended with no completed run
    by_cell: dict = {}
    for r in runs:
        if r.get("cell"):
            by_cell.setdefault(r["cell"], []).append(r)
    empty = sorted(c for c, rs in by_cell.items() if not any(x["outcome"] == "completed" for x in rs))
    if empty:
        chk.find("importante", "trazas/index.jsonl",
                 f"preregistered condition(s) {empty} have no completed run: any decision component "
                 "that depends on them cannot be evaluated", "condition_without_runs")
    return runs


# ---------------------------------------------------------------------------
# Hypothesis flag (the only write)
# ---------------------------------------------------------------------------

def set_needs_human_review(path: Path) -> bool:
    """Set `needs_human_review: true` in the note's frontmatter. Touches only that
    line (or inserts it before the closing ---); keeps its comment and the file's
    line endings. Returns True when the file changed."""
    raw = path.read_bytes().decode("utf-8")
    bom = raw.startswith("﻿")
    text = raw[1:] if bom else raw
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    if not lines or lines[0].strip() != "---":
        raise AuditError(f"{path}: no frontmatter")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise AuditError(f"{path}: frontmatter not closed")
    for i in range(1, close):
        m = re.match(r"^needs_human_review(\s*):(\s*)([^#]*?)(\s*#.*)?$", lines[i])
        if m:
            if m.group(3).strip().lower() == "true":
                return False
            lines[i] = f"needs_human_review: true{m.group(4) or ''}"
            break
    else:
        lines.insert(close, "needs_human_review: true")
    out = nl.join(lines)
    path.write_bytes((("﻿" if bom else "") + out).encode("utf-8"))
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def audit(exp_path: Path, hyp_path: Path, *, trace_path: Path | None = None,
          analysis_path: Path | None = None, plan_path: Path | None = None,
          claim_setup_path: Path | None = None, design_setup_path: Path | None = None,
          splits_path: Path | None = None, judgement_path: Path | None = None,
          apply: bool = False) -> dict:
    efm, body = _note(exp_path, "experiment note")
    hfm, _ = _note(hyp_path, "hypothesis note")
    eid = str(efm.get("id") or exp_path.stem.split(" ")[0])
    hid = str(hfm.get("id") or hyp_path.stem.split(" ")[0])
    exp_dir = exp_path.resolve().parent
    trace_path = trace_path or exp_dir / "trazas" / "index.jsonl"
    if analysis_path is None and (exp_dir / "trazas" / f"{eid}.analysis.json").exists():
        analysis_path = exp_dir / "trazas" / f"{eid}.analysis.json"
    if plan_path is None and (exp_dir / f"{eid}.data.json").exists():
        plan_path = exp_dir / f"{eid}.data.json"
    analysis = _load_json(analysis_path, "analysis record")
    plan = _load_json(plan_path, "plan")
    claim_setup = _load_json(claim_setup_path, "claim setup")
    design_setup = _load_json(design_setup_path, "design setup")
    splits = _load_json(splits_path, "splits")
    judgement = _load_json(judgement_path, "judgement") or []
    if isinstance(judgement, dict):
        judgement = judgement.get("findings", [])

    frozen_text = "\n".join(section(body, s) for s in FROZEN_SECTIONS)
    checks = {c: Check(c) for c in CHECKS}

    runs = check_selection(checks["selection"], eid, body, trace_path, analysis, plan)
    check_benchmark(checks["benchmark"], eid, hid, claim_setup, design_setup, plan)
    check_leakage(checks["leakage"], eid, efm, exp_path, frozen_text, body, runs, analysis, splits)
    check_metric(checks["metric"], eid, efm, body, analysis, live=apply)

    for j in judgement:
        c = j.get("check")
        if c not in checks or j.get("severity") not in SEVERITIES:
            raise AuditError(f"judgement entry needs check in {CHECKS} and severity in {SEVERITIES}: {j}")
        checks[c].find(j["severity"], j.get("location", "?"), j.get("message", ""), "judgement",
                       by=j.get("by", "pitfall-audit skill"))
    if not any(j.get("check") == "benchmark" for j in judgement):
        checks["benchmark"].find("importante", f"{hid} ## Claim vs {eid} frozen design",
                                 "benchmark judgement not recorded: the pitfall-audit skill must state, "
                                 "with a severity, whether this task/data tests the claim as worded",
                                 "judgement_missing")

    report_checks = {c: checks[c].as_dict() for c in CHECKS}
    all_f = [f for c in report_checks.values() for f in c["findings"]]
    worst = next((s for s in SEVERITIES if any(f["severity"] == s for f in all_f)), None)
    blocking = worst == "crítico"
    flagged = False
    if apply and blocking:
        flagged = set_needs_human_review(hyp_path)
    return {
        "tool": TOOL, "experiment": eid, "hypothesis": hid,
        "mode": "apply" if apply else "dry-run",
        "context": {"experiment_validity": str(efm.get("experiment_validity", "")),
                    "primary_hypothesis": str(efm.get("hypothesis", "")),
                    "role": str(efm.get("role", "")) or "absent (read as confirmatory)",
                    "frozen_at": str(efm.get("frozen_at", "")),
                    "sanity_checks": nested_scalars(exp_path, "sanity_checks"),
                    "trace": trace_path.as_posix(),
                    "analysis_record": analysis_path.as_posix() if analysis_path else None,
                    "analysis_reconstructed": bool((analysis or {}).get("reconstructed")),
                    "plan": plan_path.as_posix() if plan_path else None},
        "checks": report_checks,
        "counts": {s: sum(f["severity"] == s for f in all_f) for s in SEVERITIES},
        "worst": worst, "blocking": blocking,
        "needs_human_review_written": flagged,
    }


def human(rep: dict) -> str:
    out = [f"PITFALL AUDIT {rep['experiment']} -> {rep['hypothesis']} ({rep['mode']})",
           f"  context: validity={rep['context']['experiment_validity'] or '?'}; "
           f"role={rep['context']['role']}; frozen_at={rep['context']['frozen_at'] or '?'}"]
    for c in CHECKS:
        ch = rep["checks"][c]
        out.append(f"  [{c}] {'PASS' if ch['status'] == 'pass' else ch['worst'].upper()}")
        for f in sorted(ch["findings"], key=lambda f: SEVERITIES.index(f["severity"])):
            out.append(f"    - {f['severity']} @ {f['location']}: {f['message']}")
    n = rep["counts"]
    out.append(f"  => {n['crítico']} crítico, {n['importante']} importante, {n['menor']} menor; "
               + ("BLOCKS the transition" if rep["blocking"] else "does not block"))
    if rep["blocking"]:
        out.append("  needs_human_review: " + ("written (true)" if rep["needs_human_review_written"]
                                               else "not written (dry-run or already true)"))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Pitfall audit before an evidence-based status transition.")
    ap.add_argument("--version", action="version", version=TOOL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--experiment", type=Path, required=True)
    a.add_argument("--hypothesis", type=Path, required=True)
    for opt in ("trace", "analysis", "plan", "claim-setup", "design-setup", "splits", "judgement", "out"):
        a.add_argument(f"--{opt}", type=Path)
    a.add_argument("--apply", action="store_true",
                   help="on crítico, write needs_human_review: true on the hypothesis (default: dry run)")
    a.add_argument("--json", action="store_true")
    f = sub.add_parser("flag-review")
    f.add_argument("--hypothesis", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "flag-review":
            changed = set_needs_human_review(args.hypothesis)
            print(json.dumps({"tool": TOOL, "hypothesis": args.hypothesis.as_posix(),
                              "needs_human_review": True, "changed": changed}))
            return 0
        rep = audit(args.experiment, args.hypothesis, trace_path=args.trace,
                    analysis_path=args.analysis, plan_path=args.plan,
                    claim_setup_path=args.claim_setup, design_setup_path=args.design_setup,
                    splits_path=args.splits, judgement_path=args.judgement, apply=args.apply)
    except (AuditError, trace_index.TraceError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"tool": TOOL, "error": str(exc)}, ensure_ascii=False))
        return 1
    if args.out:
        args.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else human(rep))
    return 3 if rep["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
