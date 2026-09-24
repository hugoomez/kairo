# A4 — bundle isolation check: notes for the integrator

Not a change request against a file A doesn't own. These are notes on what A4 built
against `docs/v3-interfaces.md` §1a, for whoever merges `v3-block-a` and `v3-block-b`.

## What landed

| File | What |
|---|---|
| `scripts/security/check_bundle.py` | the §1a checker (stdlib only, `--version` 1.0.0) |
| `scripts/security/test_check_bundle.py` | 22 unittest tests (`python -m unittest` from `scripts/security/`) |
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
| `private_key` | block | `-----BEGIN … PRIVATE KEY-----` block in any file (text or binary) |
| `ssh_key_file` | block | `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`(`_sk`); any non-public file under `.ssh/` |
| `keystore` | block | `*.p12`, `*.pfx`, `*.jks`, `*.keystore` |
| `git_directory` | block | a `.git/` dir or `.git` gitlink file. Always blocks, whatever repo it came from, because git objects carry full history (for the vault, every note). The checker doesn't descend into it. |
| `git_credentials` | block | `.git-credentials`; a git config with `[credential]` / `helper =` |
| `credential_url` | block | `scheme://user:<non-placeholder secret>@host` |
| `api_key` | block | provider shapes (AWS AKIA/ASIA, GitHub ghp_/gho_/ghu_/ghs_/ghr_/github_pat_, Anthropic sk-ant-, OpenAI sk-/sk-proj-, HF hf_, Google AIza, Slack xox?-, Stripe) or a `*api_key/access_key/secret_key/client_secret/private_key = <real-looking value>` assignment |
| `token` | block | JWT; `Bearer <value>`; `*TOKEN/SECRET/PASSWORD/PASSWD/PWD/CREDENTIAL = <real-looking value>` |
| `netrc` | block | `.netrc`, `_netrc` |
| `credentials_file` | block | `kaggle.json`, `.pypirc`, `.npmrc`, `.aws/credentials`, `.docker/config.json`, `huggingface/token`, gcloud credential files |
| `vault_path` | block | a bundle path with a `Papers` or `Projects` segment (case-sensitive), reported once at the offending directory |
| `vault_absolute_path` | block | content with an absolute path that has a `Kairo` segment or `vault/Papers`/`vault/Projects` |
| `vault_note` | block | markdown frontmatter `id: P-/H-/E-/PROJ-/ADR-/T-<digits>` plus `project(s):`/`hypothesis:`/`linked_*`; or a file named `_digest.md`, `_hub.md`, `Estado-del-arte.md` |
| `symlink_escape` | block | a symlink or junction (or an archive link member) resolving outside the bundle. Links are never followed. |
| `env_template` | warn | `.env.example/.sample/.template/.dist/…`. Contents are still scanned, so a real key inside is still a `block`. |
| `ssh_public_file` | warn | `id_*.pub`, `known_hosts`, `authorized_keys`, `.ssh/config` |
| `git_config` | warn | `.gitconfig` / git `config` without credentials |
| `key_file` | warn | `*.key` with no private-key block |
| `vault_path_reference` | warn | content mentioning a vault-relative `Papers/…` or `Projects/…` path |
| `truncated_scan` | warn | file, member or archive bigger than the scan cap (default 8 MiB per file, 256 MiB per archive) |
| `archive_unreadable` | warn | corrupt archive or encrypted member. Its members were not scanned. |
| `archive_unscanned` | warn | `.7z`/`.rar`, or nesting deeper than 2 levels |

An unreadable file or directory has no `kind`. It is an **error** (exit 1): the check
couldn't complete.

Placeholders are never secrets: `$…`, `${…}`, `<…>`, `%(…)s`, `your/xxx/changeme/example/placeholder/dummy/redacted/…`,
`os.environ[…]` / `getenv(…)`, dotted or snake_case identifiers, values under 16 chars,
fewer than 2 character classes, or entropy below 3.0 bits/char. Env-var names alone are
never flagged.

## Test results

`cd scripts/security && python -m unittest test_check_bundle` → **22 tests, OK** on
Windows 11 / Python 3.11.9. The symlink-escape case uses a directory junction when
`os.symlink` lacks the privilege, so it runs on Windows too. It skips only if neither
link type can be created. The existing `scripts/paper_to_tool` tests still pass.

- **Contaminated bundle** (every kind above except the warn-only ones, plus a nested dir,
  zip members, tar.gz members, a tar symlink escape, a binary file, a junction): exit 2,
  `contaminated`, 35 findings, every planted one asserted. None of the planted secret
  values appear in stdout or stderr, with or without `--verbose`.
- **Clean bundle** (data manifest, lockfile, `MANIFEST.sha256`, `Tools/P-XXXX/…/TOOL.md`,
  code reading keys from `os.environ` / placeholders): exit 0, `{"status": "clean", "findings": []}`.
- Exit 1 with valid JSON on: a missing path, a file instead of a directory, a bad flag,
  no argument, an unreadable file, and an injected crash.
- Fake secrets are built at runtime by concatenation plus seeded pseudo-random bodies, so
  no literal key-shaped string is committed and GitHub push protection won't trip.

False-positive sweep: run over the full CPython 3.11 `Lib/` tree and site-packages. The
only `block` findings were 14 `private_key` hits in `Lib/test/certdata/*.pem`. Those are
real (test) private keys, so they are true positives.

## Real bundle: E-0001

The files listed in `vault/Scripts/experiments/E-0001/MANIFEST.sha256` (plus the
manifest) were copied read-only to a scratch dir outside both repos and not committed.
Result: **exit 0, `clean`**, with 3 `warn` findings:

| path | kind | why |
|---|---|---|
| `README.md` | `vault_path_reference` | provenance prose: "Implements … `Projects/early-stopping-tareas-algoritmicas/Experimentos/E-0001.md`" |
| `spec.py` | `vault_path_reference` | same provenance line in the docstring |
| `data.py` | `vault_path_reference` | the guarded local-first fallback `here.parents[2] / "Projects/…/E-0001.data.json"`, used only if the bundled copy is absent |

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
4. **Optional flags.** `--verbose` (stderr only) and `--max-bytes N` are extensions. The
   bare `check_bundle.py <bundle_dir>` call behaves exactly per §1a.
5. **Content vault references are `warn`, not `block`**, as justified above. If the
   researcher wants them hard-blocked, change one line (`vault_path_reference` severity).
   But first fix `data.py`/`spec.py`/`README.md` in E-0001/E-0002, or those bundles will
   stop transferring.
