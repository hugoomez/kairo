# A-backfill — citation-resolution fields for the existing `Papers/` notes (A1)

## Target

`Kairo/vault/Papers/P-0001 … P-0014` (owner: INTEGRATION). Frontmatter only:
adds `resolved`, `openalex_id`, `resolution_checked` (contract §1c) plus the
Block-A extension fields `resolution_status`, `resolution_match`,
`resolution_evidence`. No body text changes; each note keeps its line endings.

`<vault>` below is the vault root (`Kairo/vault` on the researcher's machine).

## Change

Run from the plugin root, after merging `v3-block-a` (preferably with
`OPENALEX_API_KEY` set — see `docs/v3-pending/A-readme.md`; keyless also works
for these 14 notes since singleton lookups are free, but a key avoids the
$0.10/day keyless budget running out mid-run):

```
python scripts/citations/resolve_refs.py --papers <vault>/Papers --write
```

then commit the 14 changed notes in the vault repo. Then, optionally, confirm the sweep:

```
python scripts/citations/retraction_sweep.py --vault <vault>
```

(`--write` on the sweep is only needed if it flags something; on 2026-09-24 it
flagged nothing.)

### Report-only dry run — 2026-09-24, keyless (`OPENALEX_API_KEY` not set)

Command: `resolve_refs.py --papers <vault>/Papers --json`
(no `--write`; the vault was not modified). Raw JSON kept outside the repo in
the A1 session scratchpad (`backfill.json`). Summary: **13 resolved, 1
unresolved, 0 mismatch, 0 retracted, 0 withdrawn**.

| P-id | status | match | openalex_id | diff | sources (state / match) | flags |
|---|---|---|---|---|---|---|
| P-0001 | resolved | exact | W4226434736 | — | openalex found (exact), arxiv found (exact) | — |
| P-0002 | resolved | close | W4316135772 | title `source_error` (see below) | openalex found (close), arxiv found (exact) | importante |
| P-0003 | **unresolved** | exact (arXiv only) | — | — | openalex not_found, arxiv found (exact), semantic_scholar lost (HTTP 429) | importante |
| P-0004 | resolved | exact | W3043650391 | — | openalex found (exact), arxiv found (exact) | — |
| P-0005 | resolved | exact | W4324301516 | — | openalex found (exact), arxiv found (exact) | — |
| P-0006 | resolved | exact | W4281390462 | — | openalex found via arXiv DOI (exact), arxiv found (exact) | — |
| P-0007 | resolved | exact | W4386559757 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |
| P-0008 | resolved | exact | W4320170046 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |
| P-0009 | resolved | exact | W4315588185 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |
| P-0010 | resolved | exact | W4406231765 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |
| P-0011 | resolved | exact | W4254751698 | — | openalex (exact), crossref (exact) | — |
| P-0012 | resolved | exact | W2156876426 | — | openalex via title search (exact) — the note has no DOI/arXiv id | — |
| P-0013 | resolved | exact | W7130703375 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |
| P-0014 | resolved | exact | W4387891174 | — | openalex via arXiv DOI (exact), arxiv (exact) | — |

Retraction / withdrawal: all 14 clear (Crossref for P-0011; arXiv for the 12
arXiv notes; OpenAlex `is_retracted` false on all 13 resolved works).

### Mismatches

**None.** No note's title / first author / year disagrees with its
identifier's registrar record (arXiv or Crossref). Two items need a human look,
neither of them a note error the script should fix:

- **P-0002 — OpenAlex data error, not a note error.** OpenAlex's record for
  `10.48550/arXiv.2301.05217` (W4316135772) has the right DOI, authors
  (Nanda, Chan, Lieberum, Smith, Steinhardt) and year (2023), but its title is
  *"Certified Grokking: a machine-checked certificate for the Nanda et al.
  modular-addition transformer"*. arXiv's own record matches the note exactly.
  The script accepts the id (title level `source_error`, flag `importante`).
  Optionally report the bad title to OpenAlex.
- **P-0003 — no OpenAlex record for the arXiv version.** OpenAlex has no work
  for `10.48550/arXiv.1912.02292`; arXiv confirms the note exactly. OpenAlex's
  title search finds W2994081359, the *journal* version (J. Stat. Mech. 2021,
  DOI 10.1088/1742-5468/ac3a74), rejected because its year (2021) is two away
  from the note's (2019). Under the contract this stays `resolved: false`
  (fallback-only). Researcher decision: keep citing the 2019 preprint (stays
  unresolved), or switch the note to the journal version (DOI + year 2021)
  and re-run, which would resolve to W2994081359. **Not changed here.**

### Sweep (report mode) — 2026-09-24

`retraction_sweep.py --vault <vault> --json`: 14 papers
checked, **0 retracted, 0 withdrawn, 0 concern**, 0 lost checks, 0 citing notes
flagged. Nothing written.

## Verify

1. After the `--write` run, every `Papers/P-*.md` has all three §1c fields;
   `resolved: true` exactly on the notes with a `W\d+` `openalex_id`:
   `python -c "import glob,re; [print(f) for f in glob.glob('<vault>/Papers/P-*.md') if (lambda t: ('resolved: true' in t) != bool(re.search(r'^openalex_id: W\d+', t, re.M)))(open(f,encoding='utf-8').read())]"`
   prints nothing.
2. The summary line matches the table above (13 resolved / 1 unresolved), or any
   difference is explained by upstream changes (OpenAlex adding the P-0003
   preprint, etc.).
3. `git -C <vault> diff --stat -- Papers` shows only
   frontmatter additions (no deleted lines) in 14 files.
4. `resolve_refs.py --papers … --gate --only P-0001 P-0011` exits 0; `--gate`
   over all 14 exits 2 (P-0003) until P-0003 is decided.
