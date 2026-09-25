# A-backfill — citation-resolution fields for the existing `Papers/` notes (A1)

## Target

`Kairo/vault/Papers/P-*.md` (all current notes) (owner: INTEGRATION). Frontmatter only:
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

### Report-only dry run — 2026-09-24

A report-only run (`--json`, no `--write`, keyless) was made over every note
on 2026-09-24. **Its per-note results are not kept in this repo**, because
they would put the vault's private library into the public plugin. The
Block A session report has them. Summary:

- 13 resolved, 1 unresolved (arXiv-only preprint, no OpenAlex record), 0 mismatch,
  0 retracted, 0 withdrawn.
- 1 OpenAlex data error (OpenAlex holds a wrong title for one arXiv DOI; the
  arXiv record matches the note). After the review fix, `--gate` fails on this
  case until the researcher confirms it by hand.
- Sweep: 14 checked, 0 retracted / withdrawn / concern, 0 citing notes flagged.

Re-run it at integration (the command above without `--write`, plus `--json`)
and compare with the session report before writing.

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
4. `resolve_refs.py --papers … --gate --only P-0001 P-0011` exits 0. `--gate`
   over all notes exits 2 until the researcher decides both flagged notes: the
   arXiv-only preprint (unresolved) and the OpenAlex title error (`source_error`,
   confirmed by hand).
