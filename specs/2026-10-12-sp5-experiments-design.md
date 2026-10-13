# SP5: experiments, comparisons and remote workers

- **Status:** approved by Davide on 2026-10-12 ("all as recommended"), including Q1–Q12 and D92–D105. Nothing is implemented; approval is not verification evidence.
- **Target:** Simulatte 0.17, based on released 0.16.0, commit `6251e5f9099c18465c421c564ad0f6dbae9c0ff3` (PR #65).
- **Authority:** [global design](2026-10-08-studio-global-design.md) C4, C5.1–C5.5, §§6–9; [decision log](studio-decisions.md), especially D17–D20, D23, D29, D31–D34, D37, D45, D47, D71–D91.
- **Plan:** [sequenced implementation plan](../plans/2026-10-12-sp5-experiments.md).
- **Predecessor:** [SP4 design](2026-10-11-sp4-studio-design.md) and [acceptance evidence](research/2026-10-11-sp4-acceptance.md). Their "implementation in progress" and "unreleased" wording is historical: PR #65 merged and 0.16.0 is on PyPI.
- **Decisions:** §3 lists the questions put to Davide; every recommended option was chosen and recorded as D92–D105 in the decision log. The author's other calls stand unless overridden.

## 1. Outcome and boundaries

A user defines an experiment (parameter grid or explicit configurations, optional layout variants, N replications, a stopping policy) in Python, in a JSON file or in Studio, and runs it on a pool of local and SSH workers. Each replication is an ordinary SP4 execution request executed at-least-once in a fresh process; results are published through the existing durable, fenced publication. Studio and the CLI report replication-level statistics (t intervals, insufficient-data states), paired comparisons by replication index with missing pairs listed, KPI tables, overlaid series, a Welch warm-up aid, and synchronized side-by-side playback of two full recordings. A KPI-only replication can be replayed at `full` level and verified against its stored fingerprint.

Remote workers are POSIX hosts reached through the system `ssh` client and the user's SSH configuration. The project must be a uv project for remote runs; workers provision external dependencies with `uv sync --locked` into immutable cached environments and load local packages from the shipped, verified source snapshot. uv is installed only by `simulatte workers setup --install-uv` after an explicit confirmation. Credentials are never shipped.

Out of scope for 0.17: live control of a running simulation, warm-spare execution processes before measurement (§11), remote hosts on Windows, automatic result deletion, multiple-comparison procedures beyond Bonferroni, worker discovery, and any change to the trace container or domain events. Single interactive runs, `simulatte run`, reproduction, plain scripts and `Runner` keep their 0.16 behaviour.

## 2. Inspected foundation and required extensions

Inspection was against `6251e5f`, not the SP4 spec's planning text.

| Existing code | Reuse and required extension |
| --- | --- |
| `execution/schema.py`: `ExecutionRequest` (model, source/input/layout hashes, `layout_revision`, parameters, seed, stopping, level, kpis, `runtime`), `request_id`, `AttemptKey`, `ResolvedManifest` | Reuse unchanged for replications. The `runtime` mapping gains an optional `kind: "uv"` provisioning spec for remote requests; local requests keep the exact 0.16 mapping so stored request ids do not change. |
| `execution/store.py`: schema 1, `state` singleton (`generation`, one `active` attempt), `_require_active` (compares the attempt's generation with the global `state.generation`), `_assign` (refuses a non-current revision), `bind_ready_view` (refuses a non-current revision), `revoke`, `recover` | Schema 2 (§10): a `slots` table replaces the single `active`; attempts record slot, worker and pinned revision. The fence becomes "this attempt is its slot's active assignment and its generation equals the slot's recorded generation"; the global counter only allocates unique generations. Experiment and replay assignments skip the current-revision check (they run at the pinned revision) and never bind presentation views. The interactive slot keeps the SP4 checks verbatim. |
| `execution/coordinator.py`: one `_pending` job, one `_worker`, `_run_job` loop, `prepare`, `make_request`, `_preparation`, `submit`, `cancel`, `wait`, `reproduce`, bounded `_events` | Generalise into a scheduler with K slots, each owning one transport. `_run_job` becomes a per-slot actor with unchanged message handling. The interactive slot keeps SP4 semantics (revision changes supersede it); experiment slots are fenced by experiment state. |
| `execution/transport.py`: `FramedChannel`, `LocalTransport.start` (`sys.executable -I -S`, frozen runtime root first) | Add `SshTransport` with the same `send/receive/close` interface; the channel, lanes and bounds are reused. |
| `execution/supervisor.py`: `worker_hello` (`slots: 1`, `warm_spares: 0`), one `ChildProcess` at a time, `tempfile` attempt dirs | Keep one child per supervisor. Add `stage`/`provision` handling for remote hosts (§9); the run path is unchanged once paths are local to the host. |
| `execution/driver.py`, `bootstrap.py`, `runtime.py`: `verify_runtime(config, request.runtime)` compares local facts | Branch on `runtime.kind`: local evidence as today; `uv` evidence written by the worker at provisioning and verified again in the child. |
| `execution/snapshot.py`: content-addressed source/input archives, `freeze_runtime` (tools/<hash>, the `simulatte` package only; its dependencies simpy/msgpack/tabulate/tqdm come from the environment) | Reuse as the remote bundle: archives, manifest and hashes already exist and are verified at materialization. The frozen-runtime archive adds a pure-Python `msgpack` copy for the remote supervisor only; the child's dependencies come from the provisioned environment, so remote projects must depend on `simulatte` in their lock (§9 preflight). |
| `execution/inputs.py`: `InputOwner` options → request; `_commit` revokes the active attempt on each revision | Experiments bypass `InputOwner`: they expand requests from a pinned `PreparedModel` and layout variant documents. Revisions must revoke only the interactive slot. |
| `execution/command.py`, `cli.py`: `run`, `studio`, `worker --stdio`, `view` | Add `experiment`, `replay` and `workers` subcommands (§7). |
| `stopping.py`: `Horizon(until, warmup)`, `FinitePopulation`, `Population` | The three experiment types of C5.5 map onto these policies: steady-state = `Horizon` with `warmup > 0`; terminating fixed horizon = `Horizon` with `warmup = 0`; terminating finite population = `FinitePopulation`. No new policy. |
| `kpi.py`, `trace/`: scalars `"<scope>/<kpi>"` in `results.kpis` and the footer fingerprint; `Trace.kpi_series()`; digest computed in `kpi` and `full` levels (D31) | Replication observations are the stored scalars. Series overlays and the Welch aid read `kpi_series()` from committed traces. Replay verification compares digest and common KPI scalars. |
| `studio/app.py`, `websocket.py`: `COMMANDS` whitelist, `_command` thread chain, `snapshot()` with one `active` and 20 `results`, `_pump` keyed by attempt | Add experiment, comparison, replay and worker commands and notifications; `snapshot()` gains `experiments`, `workers` and `replays`; `_pump` must route events for attempts outside the interactive slot. |
| `studio/apps/viewer`: single `Controller` with one `Session`/`Playback`/`Scene`; `Playback` takes an injectable `Clock`; `Scene` supports several live instances; KPI charts are one uPlot per series from one recording; `RunHistory` lists 20 results | Add React panels for experiments, results and comparisons; a `ComparePlayback` with two sessions on one clock in its own host (not a second `Controller`); multi-recording overlay charts. |
| `benchmarks/studio_execution.py` (cold costs: submit→ready 0.6–0.8 s CPython), `bench_replications.py` (`Runner` throughput) | Add a coordinator throughput benchmark (§11) before fixing slot defaults or warm spares. |

No experiment, statistics, scheduler, SSH transport, provisioning or comparison code exists. `tests/core/test_studio_websocket.py` uses `experiment.start` as its example of an unknown command; that example changes.

Two facts about identity shape §4.2 and need Q11/Q12: `request_id` includes `layout_revision` (D91) and `Coordinator.prepare()` creates a new revision on every call, so request ids differ between CLI invocations even on identical content; and `capture_source` excludes only the configured layout file, so a git-tracked experiment file or layout variant is source today, and editing one changes the source hash and supersedes the interactive attempt.

## 3. Decisions put to Davide (all answered with the recommendation)

Each item gives the options and the recommendation that was chosen; the decision log entries D92–D105 are authoritative.

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| Q1 | **Experiment file format** | (a) JSON `*.experiment.json` with a JSON schema `experiment-v1`, mirroring `layout.json`/`layout-v1.schema.json`; Studio can write it with stdlib. (b) TOML: nicer to hand-write, stdlib read-only, Studio would need a TOML writer dependency. | (a) JSON. The Python API and Studio produce the same dict; the file is one of three equal front ends. |
| Q2 | **Worker configuration** | (a) User-level `~/.config/simulatte/workers.toml` (`SIMULATTE_WORKERS` overrides the path), hosts named and referenced by name from experiments and CLI. (b) Project-level `[tool.simulatte.workers]` in `pyproject.toml`. (c) Both, project entries overriding. | (a). Hosts, slot counts and cache paths are personal, not project facts, and must not travel in bundles. The experiment file may list worker names; unknown names fail before scheduling. |
| Q3 | **Remote supervisor bootstrap** | (a) Ship the coordinator's frozen trusted runtime (Simulatte package plus a pure-Python `msgpack` copy) to `<cache>/runtime/<hash>/` through a one-off `ssh` staging step, then run `uv run --no-project --python <spec> python -I -S <runtime>/…` for the supervisor. Works for development versions and offline caches. (b) `uvx simulatte==<version> worker --stdio` from PyPI: no staging, but impossible for unreleased versions and requires network for the supervisor itself. | (a). It keeps one trusted runtime identity (`trusted_runtime_hash`) on both ends and makes the worker independent of PyPI availability. |
| Q4 | **Store retention** | (a) Manual only in 0.17: `simulatte experiment delete`, `simulatte store prune` (unreferenced results, orphaned snapshots), Studio delete actions; the whole store stays "safe to delete". (b) Automatic size cap with least-recently-used deletion of interactive results. | (a). Automatic deletion of results that comparisons may still reference needs reference counting across experiments, replays and views; defer until there is evidence that stores grow beyond manual control. |
| Q5 | **Warm-up aids** | (a) Welch moving-average plot of a series KPI averaged over the replications of one configuration, window chosen in the UI; warm-up stays user-set. (b) Defer all aids. | (a), minimal. The data (KPI series per replication) is already stored; the computation is small and pure Python. |
| Q6 | **Bundle manifest for remote runs** | Reuse the SP4 snapshot as the bundle (git-tracked members plus `[tool.simulatte.bundle] include`, declared inputs, minus excludes), plus `uv.lock`, each layout variant and the request documents. Default remote bundle cap 64 MiB (configurable). Path and workspace dependencies inside the root are bundled as local packages and excluded from `uv sync`; outside the root they are rejected. Non-PyPI indexes produce a preflight warning, not a failure: the host must hold its own credentials. | Confirm. Alternative: fail on private indexes (safer, but blocks legitimate setups). |
| Q7 | **Lost replications and restart** | Re-dispatch a replication automatically only when its attempt ended `lost` (transport or worker death), at most twice; `failed`/`validation_failed`/`disk_full` never retry automatically. On coordinator restart, running experiments become `paused` (active attempts are already marked lost by recovery) and resume only on explicit command or Studio action; no user code runs on startup unasked. | Confirm. Alternative: resume automatically when Studio starts. |
| Q8 | **Local slot default and warm spares** | Default one local experiment slot plus the interactive slot until the throughput benchmark (§11) is in; the measured numbers set the 0.17 default (`cpu_count - 1` capped at 4 is the expected outcome) and decide whether warm spares enter 0.17 or stay at D76. | Confirm the measure-first rule. |
| Q9 | **Statistics scope** | Pure-Python Student t quantiles (no scipy), sample mean/SD, t intervals at a configurable level, Bonferroni as the only multiple-comparison option, paired differences by replication index. Intervals are labelled pointwise unless Bonferroni is on. | Confirm. |
| Q10 | **Side-by-side playback scope** | Two committed `full` results on one clock, chosen from any experiment or interactive history; KPI-only results show a "replay first" action. No three-way or live comparison. | Confirm. |
| Q11 | **Result reuse identity** (raised by the builder check) | `request_id` includes `layout_revision`, so identical content prepared twice has two ids and exact-id reuse never happens across CLI invocations. (a) Reuse by exact request id only; drop the "same run id across invocations" claim; experiments re-execute on every `experiment run`. (b) Add a `content_id` (the request dict minus `layout_revision`, domain `request-content-v1`) stored and indexed next to each request; a replication whose content id has a committed result is `reused`; the experiment run id is built from content ids. `request_id` and D91 are unchanged. (c) Full C5.1/B6 reuse across different layout hashes through the physical dependency closure. | (b). One column and one index; honest about what is equal; (c) stays deferred. |
| Q12 | **Experiment files and layout variants versus source identity** (builder check) | Today a tracked `*.experiment.json` or `layouts/wide.json` is source: editing one changes `source_hash`, invalidates every request and supersedes the interactive run. (a) Reserve `*.experiment.json` and the files listed under a new `[tool.simulatte.bundle] layouts = [...]` setting as non-source kinds, excluded from `capture_source` like the configured layout, watched as their own change class, shipped as documents. (b) Keep them as source and accept the invalidation. | (a). Without it, saving an experiment from Studio reruns the interactive model. |

## 4. Experiments

### 4.1 Definition

`experiment-v1` is a JSON document (Q1) with a published schema at `src/simulatte/experiments/schema/experiment-v1.schema.json`:

```json
{
  "schema": 1,
  "name": "servers-sweep",
  "model": "plant.py:build",
  "replications": {"count": 10, "base_seed": 0},
  "configurations": {"grid": {"servers": [2, 3, 4], "wip_norm": [10, 20]}},
  "layouts": [{"label": "base", "file": "layout.json"}, {"label": "wide", "file": "layouts/wide.json"}],
  "stopping": null,
  "level": "kpi",
  "kpis": [],
  "workers": ["local", "strangelove"]
}
```

- `configurations` is either `{"grid": {name: [values]}}` (Cartesian product in declaration order) or `{"explicit": [{"label": "...", "parameters": {...}}, ...]}`. Values are validated against the model's parameter schema at definition time; required parameters without a grid entry take the model default or fail.
- `layouts` is optional; absent means the configured project layout. Each entry is a project-relative regular file validated as layout-v1. A **configuration** is one parameter set × one layout variant; labels are `<params-label>/<layout-label>` with deterministic parameter labels (`servers=2,wip_norm=10`) unless explicit.
- `replications.count` ≥ 1; seed of replication *i* is `base_seed + i`, required to stay below 2⁶³. Every configuration uses the same seed for the same replication index (common random numbers, D23), which is what makes paired comparisons by index valid.
- `stopping` null uses the model's declared policy; otherwise a normalized `Horizon`/`FinitePopulation` dict. The experiment **type** of C5.5 is derived from the policy (§2) and reported, not declared separately.
- `level` defaults to `kpi` (D17); `full` is allowed and warned about for large counts. `kpis` is the SP4 selection; empty records all.
- `workers` lists worker names from the user configuration (Q2) or `local`; absent means the default pool (`local` only).
- Product limits: at most 10 000 configurations and 1 000 000 replications per experiment, and at most 64 grid parameters; these are validation errors.

Python API (`simulatte.experiments`): `Experiment`, `Grid`, `Explicit`, `LayoutVariant`, `Replications`, `load_experiment(path)`, `Experiment.to_dict()`/`from_dict()`; `simulatte.experiments.run(experiment, project=None, workers=None, timeout=None) -> ExperimentRun` for scripts, built on the headless coordinator. Studio edits the same dict in a form and saves the file through the existing owned-write path (symlink-safe, revision-checked).

### 4.2 Identity and expansion

- `experiment_id = canonical_hash("experiment-v1", definition)`. Definitions are immutable; editing creates a new experiment.
- An **experiment run** pins what the definition does not: the prepared model (source and input hashes, model metadata), each layout variant's normalized content hash and document, the requested runtime per worker kind (local facts or uv provisioning spec, §9), and the resolved stopping policy. With Q11(b), `run_id = canonical_hash("experiment-run-v1", {experiment_id, sorted content ids})`, which is stable across invocations on unchanged inputs; the pinned project revision is stored on the run row, not hashed. With Q11(a), the run id includes the revision and every `experiment run` is a new run.
- Expansion creates one `ExecutionRequest` per configuration × replication, with `layout_revision` fixed to the pinned revision and `runtime` set per target worker kind. Requests for local and remote workers differ in `runtime` and therefore in id; a replication is bound to one worker kind when first dispatched, recorded in the store, and keeps that identity on re-dispatch so a result pairs with its request.
- With Q11(b), a replication whose content id already has a committed result (from an earlier run, an interactive run or a replay) is marked `reused` and not executed; the report shows reused counts. When an experiment mixes local and remote workers, replications of one configuration may run on different dependency sets; the report flags `mixed_runtime` per configuration when resolved dependency hashes differ, and comparisons still proceed.

### 4.3 Scheduling and durable state

- The coordinator owns a set of **slots**: `interactive` (local, SP4 semantics, also used by `simulatte run`), `local-<n>` experiment slots, and `<worker>-<n>` remote slots. Each slot owns one transport (one supervisor, one child at a time). The preview slot of D89 is unchanged.
- Pending replications are ordered replication-major: `(replication index, configuration index)`. Early replications of every configuration complete first, so paired statistics become available as soon as two replications exist for all configurations.
- Dispatch is durable: assigning a replication to a slot writes `attempts(slot, worker)` and the slot's active attempt in one transaction (schema 2). Only the slot's current assignment may publish. Experiment slots are not revoked by project revisions; the interactive slot keeps its SP4 supersession.
- Replication states: `pending → dispatched → succeeded | failed | cancelled | lost → (pending, bounded) | reused`. Run states: `running → completed` (all replications terminal and at least one succeeded), `cancelled`, `paused` (after restart, Q7). Cancelling a run cancels its active attempts through the existing attempt cancellation, then marks pending replications `cancelled`.
- Progress events: `experiment_progress` with counts per state and per configuration, coalesced like `progress`. Per-attempt `trace_available`/`kpi` events keep flowing for live inspection of any replication.
- A storage failure that prevents durable state (SP4 `index_terminal_state: not_durable`) closes admission for all slots.

## 5. Statistics and comparisons

`simulatte/statistics.py` (core, pure Python, no scipy):

- `summarize(values, *, level=0.95) -> Estimate(n, mean, sd, half_width, lower, upper, state)`. `state` is `ok` (n ≥ 2, sd > 0), `degenerate` (n ≥ 2, sd = 0: zero-width interval, labelled), or `insufficient` (n < 2: mean of one value or nothing, no interval). Sums use `math.fsum`.
- `t_quantile(p, df)` from the inverse regularized incomplete beta (continued fraction plus Newton/bisection), tested against reference values for df 1–30, 60, 120 and the normal limit to 1e-6.
- `paired(a: Mapping[int, float], b: Mapping[int, float], *, level) -> Paired(differences, n, estimate, missing)` pairs by replication index only; indices absent, failed or non-finite on either side are listed in `missing` and excluded. The sign is `b − a`.
- `bonferroni(level, m)` adjusts the level for m simultaneous intervals; reports expose `pointwise: bool` and the effective level.
- `welch(series: Sequence[Sequence[(t, v)]], *, window, grid)` averages replications on a common time grid (previous-value interpolation, as the KPI display rules declare) then applies a centred moving average of `window` points.

Observations are replication-level scalars from `results.kpis` (one value per KPI per replication). Samples inside a run are never treated as observations (C5.5). A KPI that a replication did not report (collector `empty` None) counts as missing for that replication.

Reports (`simulatte.experiments.results`): per configuration × KPI `Estimate`; per KPI paired comparison of each configuration against a chosen baseline; the number of succeeded replications per configuration; failed and missing replications listed by index with their attempt status. The CLI prints tables; `--json` emits one bounded envelope; Studio renders the same data.

## 6. Replays

- `replay(request_id, *, worker=None)` creates a new request equal to the original with `level="full"`, links it as `replays(replay_request_id → original_request_id)`, and prefers the original host (same `worker` name, same resolved runtime facts), otherwise any slot with an identical resolved runtime, otherwise fails with `no_compatible_worker` rather than silently running elsewhere.
- Verification after publication: the replay's trace `verify()` must be `True`; its fingerprint digest and all common KPI scalars must equal the original's; resolved runtime facts, dependency hash and physical layout hash must match. Outcome `verified`, otherwise `unverified` with the first differing field. Studio and the CLI label unverified replays prominently and still allow viewing them.
- A verified replay stands in for the original in comparisons that need events (side-by-side playback); statistics keep using the original replication's scalars.

## 7. Studio and CLI surfaces

Websocket (v1 envelope unchanged, new command types): `experiment.define` (validate and save a definition file), `experiment.start`, `experiment.cancel`, `experiment.resume`, `experiment.delete`, `comparison.get` (configuration table, baseline, level, bonferroni flag), `series.get` (keys for overlay/Welch, bounded to 8 recordings), `replay.start`, `workers.get`. Notifications: `experiment_state`, `experiment_progress` (coalesced per run), `replay_state`, `workers_state`. `state.get` adds `experiments` (bounded to the newest 20 runs with per-run counts), `workers` and `replays`. Experiment and comparison commands do not hold the Studio mutation lock during Python statistics; they fence on the run id and store state, not the project revision.

HTTP: `GET api/experiments/<run>/results` (bounded JSON, same auth), traces through the existing prefix endpoint. No new upload route; layout variants are files in the project.

Studio UI (React, inside `StudioPanel`): **Experiments** (list, definition form or file picker, start/cancel/resume/delete, per-configuration progress, worker pool status including `uv_missing`/`provisioning` states); **Results** (configuration × KPI table with mean, half-width, n and state; baseline selector; paired-difference columns; "pointwise 95 % t intervals" or "Bonferroni-adjusted" label; failed/missing pairs listed); **Charts** (overlaid series of up to 8 selected recordings on one uPlot with a shared x-scale; Welch plot with window control); **Compare** (two committed full recordings, one clock, shared transport controls, per-side KPI tiles; "Replay at full level" for KPI-only results, with the verified/unverified badge). Replication selection opens the normal viewer for that recording.

CLI:

```
simulatte experiment run FILE [--project DIR] [--workers NAME,...] [--local-slots N] [--timeout S] [--json]
simulatte experiment status [RUN_ID] [--json]
simulatte experiment results RUN_ID [--level 0.95] [--baseline LABEL] [--bonferroni] [--json]
simulatte experiment cancel|resume|delete RUN_ID
simulatte replay REQUEST_ID [--worker NAME] [--json]
simulatte workers check NAME            # connectivity, platform, shell, uv, cache path; read-only
simulatte workers setup NAME [--install-uv]   # shows the plan, asks y/N before installing uv
simulatte store prune [--dry-run]
```

Exit codes follow SP4 (0, 1, 2, 130). `experiment run` waits for completion and prints the results table; `--json` prints the report envelope.

## 8. Worker pool and SSH transport

- Configuration (Q2): `~/.config/simulatte/workers.toml`, read with `tomllib`, validated strictly:

  ```toml
  [workers.strangelove]
  host = "strangelove"        # ssh destination; user, port, keys and ControlMaster come from ~/.ssh/config
  slots = 4
  ssh_args = ["-o", "BatchMode=yes"]   # optional extras, appended after the defaults
  uv = "~/.local/bin/uv"      # optional explicit path on the host
  cache = "~/.cache/simulatte" # optional remote cache root
  ```

  `local` is implicit. No passwords, keys or tokens are accepted in this file; an entry with such keys is rejected.
- `SshTransport.start(worker, bootstrap)` runs `ssh -o BatchMode=yes -o ClearAllForwardings=yes <ssh_args> <host> -- sh -c '<bootstrap>'` with `subprocess.Popen` pipes (`bufsize=0`), wraps stdout/stdin in `FramedChannel`, and drains stderr as diagnostics. The 64 KiB/10 s pre-magic noise budget covers MOTD and shell output. Heartbeat 1 s, loss 5 s, stalled 10 s, cancel escalation and spool bounds are unchanged; on transport loss the coordinator terminates the ssh client, marks the attempt `lost`, and the remote supervisor's stdin EOF ends its children (existing behaviour). Docs recommend `ServerAliveInterval` and `ControlMaster` in the user's SSH configuration; one SSH session per slot.
- Environment variables are not forwarded; the remote command receives only the bootstrap text. `UV_INDEX*`, `UV_*TOKEN*` and similar values present locally are never read.
- Windows: a Windows coordinator keeps local experiments (Job Object containment unchanged); SSH transport from Windows is not tested in 0.17 and `workers check` says so. Windows is never a remote host.

## 9. Remote provisioning and bundles

- **Preflight** (local, before scheduling): project root has `pyproject.toml` and `uv.lock`; `requires-python` parsed; every `[[package]]` with an `editable`/`directory`/`virtual` source resolves inside the project root (else rejected); non-PyPI registries produce a warning (Q6); bundle size within the remote cap; the lock must contain `simulatte` with the coordinator's major.minor (`incompatible_simulatte` otherwise, `missing_simulatte_dependency` when absent), because the child's own dependencies (simpy, msgpack, tabulate, tqdm) come from the provisioned environment while the `simulatte` package itself is shadowed by the frozen trusted runtime. Preflight runs no uv command locally; uv is not required on the coordinator.
- **Provisioning spec** (part of the remote request's `runtime`): `{"kind": "uv", "python", "lock_hash", "pyproject_hash", "extras", "groups", "local_packages", "trusted_runtime_hash"}`. `python` is an exact version: the project's `.python-version` when it pins one, otherwise the coordinator's own `major.minor.patch`; it must satisfy `requires-python` or preflight fails. The same value is passed to `uv run --python` for the supervisor and `uv sync --python` for the environment, so both run one interpreter and `sys.executable` in the supervisor is the environment's interpreter family. The environment cache key is this spec minus `trusted_runtime_hash` (D47: environments hold external dependencies only).
- **Provisioning bound:** `provision` carries a timeout (worker setting `provision_timeout`, default 600 s, also bounded by the attempt deadline when one is set). Heartbeats continue during `uv sync`; expiry kills the uv process group and answers `provisioning_failed` with reason `timeout`, which is a replication failure, not `lost`, and is not re-dispatched automatically. A transport loss during provisioning is `lost` as usual.
- **Staging** (protocol 1.1, capability `staging-v1`): coordinator → worker `stage {kind, sha256, length}` followed by `stage_fragment` raw frames ≤ 1 MiB under the existing 16 MiB window and `ack` credit, worker → coordinator `staged {sha256, cached}`. Kinds: `source`, `inputs` (SP4 archives), `document` (layout variant, runtime evidence, metadata, request), `runtime` (frozen trusted runtime). The worker verifies the SHA-256 before publishing a blob under `<cache>/blobs/<sha256>` and answers `cached: true` for blobs it already holds, so the second replication ships nothing. Bootstrap stages only the trusted runtime (Q3) through a separate `ssh` invocation that reads a length-prefixed, hash-checked archive from stdin.
- **Provision** (capability `uv-provisioning-v1`): `provision {spec, source_sha256}` → the worker materializes `pyproject.toml` and `uv.lock` from the staged source, runs `uv sync --locked --no-install-project --no-install-workspace --no-install-package <each local package> --no-install-package simulatte [--extra …] [--group …] --python <spec>` with `UV_PROJECT_ENVIRONMENT=<cache>/envs/<spec-hash>` under an exclusive lock, writes runtime evidence (`describe_runtime` of the environment) into the environment directory, marks it immutable (`published` marker, read-only tree) and answers `provisioned {evidence_sha256, python, site_packages}`. A published environment is never modified; a failed sync leaves no published environment. `uv_missing` and `provisioning_failed` carry uv's bounded stderr tail.
- **Run**: identical `run` message, with the config's paths pointing at the host-local materialization (`<cache>/attempts/<attempt>/snapshot`, read-only per attempt), `external_roots` = frozen runtime first, then the environment's site-packages, and document references pointing at staged blobs. The child verifies the environment against the provisioning evidence and loads local packages from the snapshot ahead of anything else, failing the attempt otherwise (D47). `project_root` is the snapshot root on remote hosts; the import policy's editable-tree checks therefore reject anything outside the snapshot and the provisioned environment.
- Remote cache layout: `<cache>/{runtime/<hash>, blobs/<sha256>, envs/<spec-hash>, attempts/<attempt>}`; attempts are removed after the supervisor reports terminal state; `simulatte workers prune NAME` removes unreferenced blobs and environments. Deleting the cache is safe.

## 10. Store schema 2 and retention

Forward migration in one transaction, `PRAGMA user_version=2`; unknown newer versions still refuse. Version 1 stores open and migrate without user action.

- `slots(name TEXT PRIMARY KEY, kind TEXT, worker TEXT, active TEXT REFERENCES attempts(id), generation INTEGER)`; `state.active` moves into `slots('interactive')` with the current `state.generation`. `state.generation` stays the global allocator of unique generations; the fence compares an attempt with its own slot's `(active, generation)`.
- `attempts` gains `slot TEXT` and `worker TEXT` (null for interactive/local); existing rows migrate to `slot='interactive'`.
- `requests` gains `content_id TEXT` with an index when Q11(b) is chosen; existing rows are backfilled from their stored bodies during migration.
- `experiments(id, name, definition BLOB, created)`, `experiment_runs(id, experiment, preparation BLOB, status, created, updated, details BLOB)`, `configurations(run, idx, label, parameters BLOB, layout_hash, layout_ref BLOB, PRIMARY KEY(run, idx))`, `replications(run, configuration, idx, seed TEXT, worker_kind, request_id, status, attempt, dispatches INTEGER, PRIMARY KEY(run, configuration, idx))`, `replays(request_id PRIMARY KEY, original TEXT, attempt, status, verified INTEGER, details BLOB)`.
- `recover()` marks active attempts `lost` in every slot, sets running experiments to `paused` and re-queues their lost replications within the dispatch bound; staging and unindexed result directories are handled as in SP4.
- Retention (Q4): nothing is deleted automatically. `experiment delete` removes a run's rows and the result directories referenced only by that run; `store prune` removes results referenced by no experiment, replay, view or the newest 20 interactive results, and snapshots referenced by no request. Both are transactional index updates followed by directory removal, so an interrupted prune leaves only unreferenced directories that the next recovery removes.

## 11. Throughput measurement before scheduling promises

`benchmarks/studio_throughput.py` runs the SP4 example model through the coordinator as an experiment of 32 replications at 100 and 20 000 events with 1, 2 and 4 local slots on eva (CPython) and strangelove (CPython and PyPy), and with 1, 2 and 4 remote slots on strangelove over SSH from eva. It reports replications per minute, per-replication overhead against `bench_replications.py` (`Runner`, same horizon), submit→ready, ready→publication, store commit time and coordinator RSS. These numbers, recorded under `specs/research/`, set the default local slot count (Q8) and decide whether a warm spare (D45/D76: one pre-imported process per slot, used once) enters 0.17. Until measured, the design promises no scheduling performance and keeps `warm_spares: 0`.

## 12. Preserved SP4 guarantees

Request identity and `layout_revision` fencing (D91); attempt fencing by `(request_id, attempt_id, generation)` plus slot assignment; durable publication with rename-then-fenced-commit and crash recovery (D77, D82); bounded transport lanes, 1 MiB fragments, 16 MiB window, spool quotas and `disk_full` (D75, D81); graceful-then-forced cancellation with guardian/process-group cleanup on POSIX and Job Objects on Windows; heartbeat loss versus `stalled` versus `backpressure`; no model imports in coordinator or supervisor; one-use execution children; no implicit uv installation; no credential shipping. Reproduction (`run --reproduce`) stays the single-run path; replays are the experiment path.

## 13. Verification and release gates

- Python: experiments, statistics, scheduler, store migration (version 1 fixtures migrate and keep request ids and results), slot fencing (a revoked experiment attempt cannot publish; interactive supersession does not touch experiment slots), lost re-dispatch bound, pause/resume on restart, replay verification (verified, digest mismatch, KPI mismatch, runtime mismatch, no compatible worker), paired statistics with missing pairs, t quantile references, Welch, SSH transport with a fake `ssh` executable (noise, EOF, hang, stderr flood), staging (hash mismatch, duplicate, window), provisioning with a fake `uv` (missing, failing sync, immutable environment), preflight rejections, worker config validation (credential keys rejected), CLI commands and JSON envelopes. Coverage stays at the 99 % branch gate; new directories join the PyPy and native CI lists.
- Real remote acceptance (not CI): strangelove (Linux x86_64, uv present) and otacon (Linux arm64, uv present) as workers from eva; bigboss (no uv) exercises `workers check` and the refusal to run plus the confirmed `setup --install-uv` path. teban is excluded. Record platform, uv and Python versions, provisioning time, bundle sizes and throughput in `specs/research/2026-xx-xx-sp5-acceptance.md`.
- TS/browser: statistics table rendering, overlay charts, Welch plot, compare playback clock synchronization and disposal (`liveScenes` accounting), replay badge; Chromium, Firefox and WebKit through Playwright; actual Safari manual checklist extended with compare playback and export.
- Windows native lane: local experiments with 2 slots, cancellation of a running experiment, migration; SSH explicitly skipped with a documented message.
- Overhead gate, old-reader verification and existing suites unchanged.

## 14. Decisions recorded in the log (D92–D105)

- **D92.** Experiment definitions are immutable `experiment-v1` JSON documents with a published schema; Python and Studio produce the same dict. (Q1)
- **D93.** Worker hosts are user configuration in `workers.toml`, referenced by name; no credentials, no discovery. (Q2)
- **D94.** Remote supervisors run the coordinator's frozen trusted runtime, staged by content hash, on a uv-managed interpreter; uv is installed only by confirmed `workers setup --install-uv`. (Q3)
- **D95.** Store schema 2: slots replace the single active attempt; experiment, replication and replay tables; retention is manual. (Q4)
- **D96.** Replications are ordinary execution requests with seed `base_seed + index` shared across configurations; expansion pins snapshots, layout variants and provisioning specs; committed results are reused.
- **D97.** Lost attempts re-dispatch at most twice; failures never auto-retry; experiments pause on restart. (Q7)
- **D98.** Statistics are replication-level, pure Python, t-based, pointwise by default with Bonferroni optional; paired comparisons pair by replication index and list missing pairs. (Q9)
- **D99.** A Welch moving-average plot is the only warm-up aid; warm-up remains user-set. (Q5)
- **D100.** Replays are `full`-level requests linked `replay-of`, preferring the original host, verified by digest, common KPI scalars and resolved runtime; unverified replays are labelled, never hidden.
- **D101.** Remote environments are keyed by the provisioning spec, hold external dependencies only, exclude local packages and `simulatte`, and are immutable once published; bundles are the SP4 snapshots plus `uv.lock` and layout variants with a 64 MiB default cap. (Q6)
- **D102.** Slot defaults and warm spares are set by the throughput benchmark, not promised in advance. (Q8)
- **D103.** Requests carry a `content_id` (request minus `layout_revision`); committed results are reused by content id; `request_id` and D91 are unchanged. (Q11)
- **D104.** `*.experiment.json` and configured layout variants are non-source inputs, excluded from source capture, watched separately and shipped as documents. (Q12)
- **D105.** Remote projects must lock `simulatte` at the coordinator's major.minor; the provisioning spec pins one exact Python version shared by supervisor and environment; provisioning has its own timeout and never counts as `lost`.
