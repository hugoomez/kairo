# Compute targets

Where a frozen experiment runs, and what the plugin owes each target. The
runners themselves live in the Kairo backend (deterministic code, never an
agent); the plugin owns the bundle, its checks, and the skills' side of the
contract.

## Declaring the target

The preregistration fixes it in the experiment note's frontmatter:

```yaml
runtime: slurm        # local | kaggle | slurm
```

(`environment.runtime` is read too.) It is frozen with the rest of the note.
Running elsewhere changes the environment manifest, so it is a `## Enmiendas`
entry, never a silent switch. An older note without `runtime` may run
anywhere, but where it ran is recorded in `## Resultado` (**importante** if
missing).

| runtime | who runs it | what the agent does |
|---|---|---|
| `local` | run-experiment in the session, after approval | everything (steps 0–8) |
| `kaggle` | the backend: private dataset + private kernel | prepare mode (bundle + check), later analyze mode |
| `slurm` | the backend: SSH upload, `sbatch`, `squeue`/`sacct`, download | prepare mode (bundle + check), later analyze mode |

## The bundle every remote target uses

One format, `kairo/kaggle-bundle@1` (the name is historical; it is not
Kaggle-specific):

```
<run_dir>/bundle.json   what to run and how it is traced
<run_dir>/bundle/       the flattened code + data manifest + lockfile, and MANIFEST.sha256
```

- `scripts/kaggle/bundle_contract.py check --run-dir D` validates the
  description, verifies every manifest line and runs
  `scripts/security/check_bundle.py` — the **crítico** isolation gate (vault
  notes, vault paths, secrets). The agent runs it before handing over; the
  backend runs it again when it registers the run and again at approval.
- `pack` writes the archive (after re-checking); `outputs` verifies what came
  back against the sha256 the entry script recorded on the remote side.
- `scripts/kaggle/kaggle_entry.py` is the remote entry: it verifies the
  manifest before running anything, enforces `max_wallclock_s` itself, and
  writes `result.json` with every output's sha256. Its input / working / scratch
  folders come from `KAIRO_KAGGLE_INPUT`, `KAIRO_KAGGLE_WORKING`,
  `KAIRO_KAGGLE_TMP`, so any target can use it.

## What a new target needs from the plugin

1. A `runtime` value (and the backend's `RUNTIMES`).
2. Nothing new in the bundle unless the platform needs it; if it does, extend
   `validate_description` in `bundle_contract.py` with tests, never loosen
   `check_bundle.py`.
3. A paragraph in run-experiment's "Where it runs" saying what the agent does
   and does not do for it (it never holds the platform's credentials and never
   calls its CLI).
4. The backend side (runner, ledger, action, cancel semantics) is described in
   the backend's own `docs/compute-targets.md`.

## Hardware cuántico (punto de extensión, no implementado)

Ningún código lo implementa. Si un proyecto necesitara una QPU de un
proveedor en la nube, encajaría así:

- **Interfaz.** Un destino más (`runtime: quantum`) con los mismos métodos que
  los demás en el backend: disponibilidad, uso semanal en la unidad que factura
  el proveedor (shots, segundos de QPU o créditos), envío = aprobación del
  investigador, consulta del estado en la cola, salidas con sha256, y una
  cancelación descrita con honestidad (muchas colas solo cancelan lo que aún no
  se ejecutó).
- **Credenciales fuera de los agentes.** El token del proveedor solo lo lee el
  backend; se retira del entorno de toda sesión de agente, y el agente no
  invoca el SDK ni la CLI del proveedor.
- **Comprobación del bundle.** El circuito y su configuración viajan en el
  bundle de siempre, con `MANIFEST.sha256`, `bundle_contract.py check` y
  `check_bundle.py` antes de cualquier envío. El preregistro congela el backend
  de QPU, los shots, la semilla del transpilador y el nivel de optimización.
- **Ledger de cuota.** Un registro local de lo que el proveedor factura por
  trabajo, una acción `run_quantum` (`off | ask`, por defecto `ask`) con su
  presupuesto semanal, la línea de bitácora y la traza como en los demás
  destinos.
- **Rigor.** El ruido del hardware es parte del diseño (controles y mitigación
  declarados en el preregistro), nunca un ajuste posterior a ver resultados.
