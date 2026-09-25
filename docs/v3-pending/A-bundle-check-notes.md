# A4 — bundle isolation check: notes for the integrator

Not a change request against a file A doesn't own. These are notes on what A4 built
against `docs/v3-interfaces.md` §1a, for whoever merges `v3-block-a` and `v3-block-b`.

## What landed

| File | What |
|---|---|
| `scripts/security/check_bundle.py` | the §1a checker (stdlib only, `--version` 1.1.0) |
| `scripts/security/test_check_bundle.py` | 40 unittest tests (`python -m unittest` from `scripts/security/`) |
| `skills/run-experiment/SKILL.md` | step 2 "External runtime": `crítico` gate before transfer; `## Resultado` records that the check ran and its status (body only, no new frontmatter); one Common mistake; one Related entry |
| `skills/paper-to-tool/SKILL.md` | step 4b: same gate before any Kaggle upload; Security bullet; Related entry |

**Block B:** `evolve-program` (B3) calls
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/check_bundle.py" <bundle_dir>` exactly per
§1a and branches only on exit code / `status`. At integration, drop B3's test double
(stub / mocked subprocess) or keep it for unit tests, and add one integration test that
runs the real script. Nothing in the contract changed.

## Contract behaviour (as implemented)

- Exit 0 `clean` / 2 `contaminated` / 1 `error`. `status` always matches the exit code.
- Stdout is one JSON object `{"status", "findings"}`, UTF-8. Each finding has exactly
  `path`, `kind`, `severity`. Diagnostics go to stderr. `--verbose` adds a stderr line per
  finding (rule name + line number). Matched values are never printed, on stdout or stderr.
- Top-level `try/except`: a crash still gives exit 1 plus valid JSON.

## Kinds

Callers never branch on `kind`. The module docstring has the full definition of each.

| kind | severity | trigger |
|---|---|---|
| `env_file` | block | `.env`, `.env.*` (except templates), `.envrc` |
| `private_key` | block | `-----BEGIN … PRIVATE KEY-----` block in any file, or a PuTTY `PuTTY-User-Key-File-<n>:` key. In binaries only with a key body after the header (bare headers are OpenSSL/SDK string constants). |
| `ssh_key_file` | block | `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`(`_sk`); `*.ppk`; any non-public file under `.ssh/` |
| `keystore` | block | `*.p12`, `*.pfx`, `*.jks`, `*.keystore` |
| `git_directory` | block | a `.git/` dir or `.git` gitlink file. Always blocks, whatever repo it came from, because git objects carry full history (for the vault, every note). The checker doesn't descend into it. |
| `git_credentials` | block | `.git-credentials`; a git config with `[credential]` / `helper =` |
| `credential_url` | block | `scheme://user:<non-placeholder secret>@host` |
| `api_key` | block | provider shapes (AWS AKIA/ASIA, GitHub ghp_/gho_/ghu_/ghs_/ghr_/github_pat_, Anthropic sk-ant-, OpenAI sk-/sk-proj-, HF hf_, Google AIza, Slack xox?-, Stripe, GitLab glpat-/gldt-/glrt-/glptt-/…) or a `*api_key/access_key/secret_key/client_secret/private_key/key/_key = <real-looking value>` assignment. `key` counts as a whole name or `_`/`-`/`.` suffix (`KAGGLE_KEY`, JSON `"key":`), not `monkey`/`hotkey`/`keys`/`key_path`/`public_key`. The name may be a subscript: `os.environ["X_API_KEY"] = "…"`. |
| `token` | block | JWT; `Bearer <value>`; `*TOKEN/SECRET/PASSWORD/PASSWD/PWD/CREDENTIAL = <real-looking value>`; `*password/passwd/passphrase/pwd/_pass = <value>` where the value may hold punctuation (`!#@…`): ≥ 12 chars, punctuation + 2 of {lower, upper, digit}, entropy ≥ 3.0, not code |
| `netrc` | block | `.netrc`, `_netrc` |
| `credentials_file` | block | `kaggle.json`, `.pypirc`, `.npmrc`, `.aws/credentials`, `.docker/config.json`, `huggingface/token`, gcloud credential files |
| `vault_path` | block | a bundle path with a `Papers` or `Projects` segment (case-sensitive), reported once at the offending directory |
| `vault_absolute_path` | block | content with an absolute path that has a `Kairo` segment or `vault/Papers`/`vault/Projects` |
| `vault_note` | block | markdown frontmatter `id: P-/H-/E-/PROJ-/ADR-/T-<digits>` plus `project(s):`/`hypothesis:`/`linked_*`; or a file named `_digest.md`, `_hub.md`, `Estado-del-arte.md` |
| `symlink_escape` | block | a symlink or junction (or an archive link member) resolving outside the bundle. Links are never followed. |
| `archive_unreadable` | **block** (was warn) | an archive (by extension or magic bytes) that can't be read: corrupt, truncated, encrypted member, unsupported compression method. Its content wasn't seen, so it can't pass. An empty (0-byte) file is not a finding. |
| `archive_unscanned` | **block** (was warn) | `.7z`/`.rar`/`.zst`/`.lz4`/`.cab`/… (extension or magic bytes), or nesting deeper than 4 levels |
| `truncated_scan` | **block** (was warn) | a top-level archive whose members decompress to more than 16 GiB in total (zip-bomb guard). Plain files never produce it: they're read to the end. |
| `env_template` | warn | `.env.example/.sample/.template/.dist/…`. Contents are still scanned, so a real key inside is still a `block`. |
| `ssh_public_file` | warn | `id_*.pub`, `known_hosts`, `authorized_keys`, `.ssh/config` |
| `git_config` | warn | `.gitconfig` / git `config` without credentials |
| `key_file` | warn | `*.key` with no private-key block |
| `vault_path_reference` | warn | content mentioning a vault-relative `Papers/…` or `Projects/…` path |

An unreadable file or directory has no `kind`. It is an **error** (exit 1): the check
couldn't complete.

**Nothing is skipped for size (v1.1.0).** A transfer gate must not pass what it didn't read.
Every file and archive member is read to the end in windows of `--max-bytes` (default
8 MiB, now the window size rather than a cap) that overlap by min(64 KiB, window/4), so a
match straddling a window edge is still seen. Archives are opened from the file on disk
and every member is streamed (no 256 MiB skip); nested archives spill to a temp file past
32 MiB. Archives are recognised by extension (zip/whl/jar/egg/office zips, tar and its
compressed forms, gz/bz2/xz) or by magic bytes, whatever the name. Text may be UTF-8 or
UTF-16 (with a BOM, or without one when every other byte is NUL).

Placeholders are never secrets: `$…`, `${…}`, `<…>`, `%(…)s`, `your/xxx/changeme/example/placeholder/dummy/redacted/…`,
`os.environ[…]` / `getenv(…)`, dotted identifiers, calls/subscripts/member expressions (`this.x++`), single-case
snake_case/UPPER_SNAKE identifiers (`default_token_v2` — not mixed-case `Ab3dE_f9GhK2mN`),
word-built CamelCase identifiers (`QuadraticTermKey`), path-shaped values
(`dir/part-0.parquet`), values under 16 chars,
fewer than 2 character classes, or entropy below 3.0 bits/char. Env-var names alone are
never flagged.

## Test results

`cd scripts/security && python -m unittest test_check_bundle` → **40 tests, OK** on
Windows 11 / Python 3.11.9. The symlink-escape case uses a directory junction when
`os.symlink` lacks the privilege, so it runs on Windows too. It skips only if neither
link type can be created. The existing `scripts/paper_to_tool` tests still pass.

- **Contaminated bundle** (every kind above except the warn-only ones, plus a nested dir,
  zip members, tar.gz members, a tar symlink escape, a binary file, a junction): exit 2,
  `contaminated`, 37 findings, every planted one asserted. None of the planted secret
  values appear in stdout or stderr, with or without `--verbose`.
- **Clean bundle** (data manifest, lockfile, `MANIFEST.sha256`, `Tools/P-XXXX/…/TOOL.md`,
  code reading keys from `os.environ` / placeholders): exit 0, `{"status": "clean", "findings": []}`.
- Exit 1 with valid JSON on: a missing path, a file instead of a directory, a bad flag,
  no argument, an unreadable file, and an injected crash.
- **Fail-closed (v1.1.0):** a 9 MiB CSV with a key on its last line, a key straddling a
  window edge, a zip member larger than the window: all found. `.7z`, rar by magic bytes,
  a corrupt zip, an encrypted zip member, a truncated `.gz`, nesting beyond 4 levels, an
  archive over the expansion ceiling: all `block`. A gzip with no `.gz` name is opened.
- **Former false negatives (v1.1.0):** `os.environ["X_API_KEY"] = "…"`, `KAGGLE_KEY=<32 hex>`,
  JSON `"key": …`, a mixed-case token containing `_`, `PASSWORD=` / `passwd:` with
  punctuation, GitLab `glpat-`, UTF-16LE without BOM, PuTTY `.ppk`, a PEM with body inside
  a binary: all block. A guard test pins `keys`/`key_path`/`monkey`/`hotkey`/`public_key`,
  type annotations, minified-JS keys, and code-valued passwords as clean.
- Fake secrets are built at runtime by concatenation plus seeded pseudo-random bodies, so
  no literal key-shaped string is committed and GitHub push protection won't trip.

False-positive sweep: run over the full CPython 3.11 `Lib/` tree and site-packages. The
only `block` findings were 14 `private_key` hits in `Lib/test/certdata/*.pem`. Those are
real (test) private keys, so they are true positives.

Re-run for v1.1.0, compared against v1.0.0 on the same trees:
- CPython 3.11 `Lib/`: 14 → 15 blocks. The one new block is `test/recursion.tar`
  (`archive_unreadable`), a deliberately malformed tar from the test suite. v1.0.0 only
  warned on it, and v1.1.0 fails closed.
- User site-packages (579 MB: numpy, pandas, pyarrow, scipy, streamlit, ortools, …): 7 → 6
  blocks. Gone: three v1.0.0 false positives (`password: OAuthFlowPassword | None`,
  `self.workbookPassword = workbookPassword`, `this._customAccessToken = customAccessToken`),
  now treated as CamelCase identifiers. New: two literal key values assigned to `*_key`
  names: pyarrow's `account_key='<Azurite emulator key>'` (a well-known public dev key)
  and a test `footer_key="0123456789abcdef"`. Both have the shape of real secrets, so
  they block. The four unchanged blocks are credential URLs and one password literal
  in library code/tests, as in v1.0.0.
- While tuning, the new rules first gave about 30 false positives: `key: SomeType`
  annotations, minified-JS `{key:`methodName`}`, `this.pass=…`, `key='a/b/part-0.parquet'`,
  and OpenSSL header strings inside DLLs. Each one is now pinned clean in
  `TestStillNotSecrets`.
- Cost: scanning to the end makes big binaries slower. Site-packages took ~24 min
  (v1.0.0: ~4 min) because the multi-MB DLLs are now read in full. A real run bundle is
  far smaller. The trade-off: the CamelCase-identifier exemption also exempts ~2% of
  random 16-char base62 values (0.7% at 20 chars, <0.03% at 32). Provider-shaped keys are
  unaffected.

## Real bundle: E-0001

The files listed in `vault/Scripts/experiments/E-0001/MANIFEST.sha256` (plus the
manifest) were copied read-only to a scratch dir outside both repos and not committed.
Result: **exit 0, `clean`**, with 3 `warn` findings:

| path | kind | why |
|---|---|---|
| `README.md` | `vault_path_reference` | provenance prose: a "Implements … `Projects/<slug>/Experimentos/E-XXXX.md`" line |
| `spec.py` | `vault_path_reference` | same provenance line in the docstring |
| `data.py` | `vault_path_reference` | a guarded local-first fallback to a `Projects/…` path, used only if the bundled copy is absent |

The full directory, including `__pycache__/`, gives the same result, and so does E-0002.
None of these is a leak. `data.py`'s fallback is exactly the "guarded local-first
fallback" that run-experiment step 2 allows. That is why a content reference to a
vault-relative path is `warn` and not `block`: blocking it would make the real, compliant
E-0001 bundle un-transferable. An *unguarded* hard-coded `Projects/…` path is still a bug
under run-experiment's rule, but the checker can't tell guarded from unguarded. The warn
surfaces it, and the skill tells the agent to list warns to the researcher. What does
block: a `Papers/`/`Projects/` **path inside the bundle** (copied vault content), an
**absolute** path into the vault, and a copied vault note.

## Contract points to review (no change requested to §1a)

1. **Archive members.** A finding inside an archive has
   `path = "<archive relpath>!<member path>"` (e.g. `extras.zip!inner/.env`). It is
   relative to `bundle_dir` in spirit but isn't a filesystem path. Callers don't branch on
   `path`, so this is harmless. If B3 ever displays paths, it should display them verbatim.
2. **Findings on error.** On exit 1 caused by an unreadable file, `findings` holds what was
   found before the failure. §1a allows "may be `[]`", so either is conformant. On a crash
   or usage error, `findings` is `[]`.
3. **`--help` / `--version`** print plain text to stdout and exit 0. These aren't contract
   invocations. A usage error (bad flag, missing arg) returns error JSON with **exit 1**,
   never argparse's default exit 2, which would read as `contaminated`.
4. **Optional flags.** `--verbose` (stderr only) and `--max-bytes N` (scan window size since
   v1.1.0; it no longer limits how much is scanned) are extensions. The
   bare `check_bundle.py <bundle_dir>` call behaves exactly per §1a.
5. **Content vault references are `warn`, not `block`**, as justified above. If the
   researcher wants them hard-blocked, change one line (`vault_path_reference` severity).
   But first fix `data.py`/`spec.py`/`README.md` in E-0001/E-0002, or those bundles will
   stop transferring.
