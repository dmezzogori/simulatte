# Experiments and comparisons

An experiment runs one model over several configurations, each with several independent replications, and reports replication-level statistics and paired comparisons. Each replication is an ordinary [managed execution](../development/studio.md#run-a-decorated-model): it runs in a fresh process, publishes through the same durable store and can be opened in Studio. Experiments are available from 0.17 and need a model declared with `@simulatte.model` (see [Model metadata and stopping](../development/studio.md#model-metadata-and-stopping-016)). All APIs are unstable.

An experiment can be defined in three equal ways: a JSON file, the Python API, or the Studio form. All three produce the same definition. This guide covers the file and the Python API, the statistics, replays and retention. Running on other machines is described in [Remote workers](remote-workers.md); the Studio panels are described in [Studio](../development/studio.md#experiments-and-comparisons-017).

## The experiment file

An experiment file is named `*.experiment.json` and follows the published schema `experiment-v1` (`src/simulatte/experiments/schema/experiment-v1.schema.json`, `$id` `https://simulatte.dev/schema/experiment-v1.schema.json`).

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
  "workers": ["local"]
}
```

| Key | Meaning |
| --- | --- |
| `schema` | Always `1`. |
| `name` | A label of up to 256 characters. Studio saves a new definition as `<name>.experiment.json`. |
| `model` | `path.py` or `path.py:name`, relative to the project, as for `simulatte run`. |
| `replications` | `count` (at least 1) and `base_seed` (default 0). The seed of replication *i* is `base_seed + i`. |
| `configurations` | Exactly one of `{"grid": {name: [values]}}` or `{"explicit": [{"label": "...", "parameters": {...}}, ...]}`. |
| `layouts` | Optional layout variants, each with a `label` and a project-relative `file`. Absent means the project's configured layout under the label `base`. |
| `stopping` | `null` uses the model's declared policy. Otherwise a normalized policy such as `{"type": "horizon", "horizon": 1000, "warmup": 100}` or `{"type": "finite_population", "arrivals": 200, "cutoff": null, "safety_limit": 5000}`. |
| `level` | `kpi` (default) or `full`. See [Recording level](#recording-level). |
| `kpis` | KPI keys to record. Empty records all of them. |
| `workers` | Names from `workers.toml`, or `local`. Empty means local only. |

The file is read strictly: unknown keys, duplicate keys, non-finite numbers and files above 16 MiB are rejected. Parameter values are checked against the model's parameter schema when the experiment is prepared; required parameters without a grid entry take the model's default or fail. Limits: 10,000 configurations, 1,000,000 replications in total and 64 grid parameters.

A grid is the Cartesian product of its lists. Parameter names are sorted alphabetically, so the order of keys in the file does not change the configurations. A **configuration** is one parameter set combined with one layout variant. Its label is `<parameters>/<layout>`, for example `servers=3,wip_norm=10/base`; explicit entries use their own label for the parameter part. Extra layout variants must be listed under `[tool.simulatte.bundle] layouts = [...]` in `pyproject.toml`, as the configured layout file does not need to be.

Definitions are immutable. The experiment id is a hash of the canonical definition, so editing the file creates a new experiment. `*.experiment.json` files and the layout files in `bundle.layouts` are not part of the captured model source, so saving or editing them does not change the source hash or restart an interactive run. In a Git project, an experiment file must be tracked or matched by `[tool.simulatte.bundle] include` to be listed in Studio.

### Run it

```bash
simulatte experiment run servers-sweep.experiment.json
simulatte experiment run servers-sweep.experiment.json --project . --local-slots 2 --timeout 600 --json
```

The project root is `--project`, or the nearest directory above the file that holds a `pyproject.toml`. The command waits for the run to finish and prints one table of replication counts per configuration; `--json` prints a single JSON envelope instead. Exit codes are 0 for `completed`, 1 for `failed`, 2 for an invalid file or arguments, and 130 for a cancelled or interrupted run. `--workers NAME,...` overrides the file's `workers`. `--timeout` bounds the preparation and each replication, in seconds.

Experiments use the project's `.simulatte` store, which has one owner at a time. Close Studio before running an experiment headlessly in the same project, or the other way round.

### Python API

```python
from simulatte.experiments import Experiment, Grid, Replications, experiment_type, run
from simulatte.stopping import Horizon

experiment = Experiment(
    name="servers-sweep",
    model="plant.py:build",
    replications=Replications(count=10, base_seed=0),
    configurations=Grid({"servers": [2, 3, 4]}),
    stopping=Horizon(1000, warmup=100),
)
print(experiment_type(experiment.stopping))  # steady_state

outcome = run(experiment, project=".")  # also accepts a path to an *.experiment.json file
print(outcome["status"], outcome["counts"])
```

`simulatte.experiments.run(experiment, project=None, workers=None, timeout=None, *, slots=None)` prepares, expands and executes the experiment and returns the final report as a dictionary (`run_id`, `status`, `counts`, and `configurations` with per-configuration counts and a `mixed_runtime` flag). `Experiment.to_dict()` and `Experiment.from_dict()` convert to and from the file format; `load_experiment(path)` reads a file. `Explicit` and `LayoutVariant` build the other forms. The statistics of a finished run are read with `simulatte.experiments.results.ExperimentReport(store, run_id)`, described below.

## Seeds and common random numbers

Replication *i* of every configuration uses the seed `base_seed + i`, which must stay below 2**63. The same replication index therefore draws from the same named random streams in every configuration (see [Seeds and RNG streams](events-and-traces.md#3-seeds-and-rng-streams)). This is what makes paired comparisons by replication index meaningful: configuration A and configuration B differ in the parameter or layout, while their replication 4 shares its random inputs. How much variance this removes depends on how closely the model's stream use lines up between configurations.

Each replication is executed at least once, in a fresh process: a replication whose worker was lost is run again with the same request and therefore the same seed.

## Three experiment types, derived from the stopping policy

The kind of experiment is not declared. It follows from the effective stopping policy, the file's `stopping` or else the model's:

| Policy | Type | Typical use |
| --- | --- | --- |
| `Horizon` with `warmup > 0` | steady state | Long-run averages after the transient has been discarded. |
| `Horizon` with `warmup = 0` | terminating, fixed horizon | A system that really starts empty and stops at a known time. |
| `FinitePopulation` | terminating, finite population | A fixed batch of work that runs until it is all done. |

`simulatte.experiments.experiment_type(stopping)` returns `steady_state`, `terminating_horizon` or `terminating_finite`. The interval statistics are the same in all three. The type decides whether the replication means are meaningful: for steady state, the warm-up must be long enough that the transient is gone, and Simulatte does not choose it for you (see [Choosing a warm-up](#choosing-a-warm-up)).

## Statistics

Observations are **replication-level scalars**: one value per KPI per replication, taken from the stored result (`"<scope>/<kpi>"`, for example `shopfloor/throughput`). Samples inside a run, such as the waiting time of each job, are never treated as independent observations. A replication that failed, was cancelled, is still pending, or did not report a KPI (an empty collector, a non-finite value) contributes nothing to that KPI.

```bash
simulatte experiment results RUN_ID
simulatte experiment results RUN_ID --level 0.9 --baseline "servers=2/base" --bonferroni --json
```

The first table has one row per configuration and KPI, with `n`, the mean, the half-width and bounds of a two-sided Student t interval on the replication means, and a state. The t intervals assume independent replications whose means are approximately normal. They are computed in pure Python; the quantile function is tested against published reference values for 1 to 30, 60 and 120 degrees of freedom and the normal limit, to 1e-6.

| State | Meaning |
| --- | --- |
| `ok` | At least two observations and a positive sample standard deviation. |
| `degenerate` | At least two observations, all equal. The interval has zero width and says so; it is not evidence of precision. |
| `insufficient` | Fewer than two observations. The mean of one value is shown, with no interval. |

### Pointwise intervals and Bonferroni

By default every interval is labelled **pointwise**: each one has the requested confidence level (`--level`, default 0.95) on its own, with no correction for looking at many of them. With `--bonferroni` the level of each interval is adjusted to `1 - (1 - level) / m`, so that the whole family of `m` intervals has at least the requested level. The output then says "Bonferroni-adjusted" and shows the family level, the effective level and `m`. Bonferroni is the only multiple-comparison method offered.

The results table and the paired comparisons are **separate families** with their own `m`. The table's `m` is the number of configuration and KPI rows shown. The paired family has `m` equal to the number of other configurations times the number of KPIs. Adjusting each family does not give one joint guarantee over both. Studio shows the same labels.

### Paired comparisons and missing pairs

With `--baseline LABEL`, every other configuration is compared with the baseline on each KPI. The differences are **configuration minus baseline**, paired by replication index only. An index that is missing on either side is not paired: the failed, cancelled or unreported replications on either side are listed under `missing` for that row, excluded from the interval, and shown in the list of replications without a result ("Missing pairs" in Studio), together with their status and attempt. `n` counts only the pairs that were used, so a paired row with fewer pairs has a wider interval and may be `insufficient`. Replications are never paired by position after dropping some.

Documents are bounded: at most 1,000 missing and 1,000 replication entries are listed; `missing_total` and `replications_total` give the full counts.

`ExperimentReport.to_dict(level=0.95, baseline=None, bonferroni=False, configuration=None)` returns the same data as the JSON envelope, with `pointwise`, `m`, `effective_level`, the rows, the `paired` rows and the missing replications. Opening the store directly looks like this:

```python
from simulatte.execution.store import RunStore
from simulatte.experiments.results import ExperimentReport

with RunStore.open(".simulatte") as store:
    report = ExperimentReport(store, outcome["run_id"]).to_dict(0.95, baseline="servers=2/base")
print(report["pointwise"], len(report["paired"]))
```

A configuration whose replications resolved different dependency sets (for example local and remote workers in one run) is flagged `mixed_runtime`. Comparisons still proceed; read them with that in mind.

### Choosing a warm-up

For steady-state experiments Studio offers a **Welch plot**. It averages one KPI series over the replications of one configuration on a common time grid, using the previous value at each grid point, then applies a centred moving average. The window is an odd number of grid points, 5 by default. Where the averaged curve flattens is a guide for the warm-up. The warm-up stays your choice: Simulatte never sets it. The plot averages at most 64 replications on a 500-point grid, and the same computation is available as `simulatte.statistics.welch`.

## Recording level

`level: "kpi"` (the default) records the KPIs and the semantic fingerprint, which is enough for all statistics and keeps replications small. `level: "full"` also records the complete trace for every replication; it is allowed but costs time and disk for large experiments. A KPI-only replication can be replayed at full level afterwards.

## Resuming, cancelling and failures

A run has one of five states: `running`, `paused`, `completed`, `failed` or `cancelled`. Replications go from `pending` to `dispatched` and then to `succeeded`, `failed`, `cancelled` or `lost`; `reused` marks a replication satisfied by an earlier result.

- Ctrl-C during `experiment run` pauses the run and exits with 130. The envelope names the run and says `experiment resume RUN_ID`. Replications that were running return to `pending` without using up a retry.
- Running the same unchanged file again does not start a second run. The run id is derived from the content of the requests, so the call returns the existing run: it resumes a paused one and reports the final state of a finished one. Replications whose content already has a committed result, from this experiment, another one or an interactive run, are marked `reused` and not executed again.
- A replication whose attempt is `lost` (the worker or transport died) is dispatched again with the same request, at most twice more, so at most three dispatches in total. A `failed` replication, including `validation_failed`, `disk_full` and `runtime_changed`, is never retried automatically.
- A restart pauses running experiments; nothing resumes on its own.
- A run is `completed` when every replication is terminal and at least one succeeded.

```bash
simulatte experiment status              # the newest runs
simulatte experiment status RUN_ID       # counts per configuration
simulatte experiment resume RUN_ID       # paused runs only
simulatte experiment cancel RUN_ID       # a paused run
simulatte experiment delete RUN_ID
```

`experiment run` and `experiment resume` accept `--local-slots N` and `--timeout S`; every `experiment` command accepts `--project DIR` and `--json`.

### Local slots

A coordinator runs experiments on several local slots, each owning one worker process and running one replication at a time. The default is `min(4, max(1, cpu_count - 1))`, and `--local-slots` overrides it. The default comes from a throughput measurement; the figures are single observations, not a promise.

| Host | Events per replication | 1 slot | 2 slots | 4 slots |
| --- | --- | --- | --- | --- |
| eva (MacBook Pro, Apple M1 Pro, 10 cores, CPython 3.14.1), load average 2.1 to 2.6 | 100 | 67.7 | 143.1 | 285.5 |
| eva, same run | 20,000 | 44.8 | 83.3 | 125.8 |

The unit is replications per minute, for 32 replications at `full` level, one process per cell (`specs/research/2026-10-12-sp5-throughput.md`, the re-run with the scheduler). Cost is dominated by process start and storage, not by the simulation: at 20,000 events the store commit took 0.33 to 0.35 s per replication on every slot count, and a one-slot flow at that size spent about 0.9 s between `ready` and publication. strangelove (Ryzen 7 3800X) was measured with one slot only: 85.9 and 44.7 replications per minute on CPython 3.14.0. The same note records PyPy 8.0.0 as slower there (46.5 replications per minute with 25 of 32 completed, and 33.1) and that some PyPy sequences stopped with an intermittent failure inside `hashlib.blake2b`; use CPython for experiments until that is understood. Warm spare processes did not enter 0.17.

## Replays

A KPI-only replication has no events to watch. `simulatte replay REQUEST_ID` runs the same request again at `full` level, links it to the original, and compares the two:

```bash
simulatte replay REQUEST_ID [--project DIR] [--worker NAME] [--timeout S] [--json]
```

The replay is **verified** only if its trace verifies and it reproduces the original's semantic digest, every KPI scalar both results report, and the original's resolved runtime facts, dependencies and physical layout hash. Otherwise it is **unverified** and the first failing check is the reason; the replay stays published and can still be opened. Exit code 0 means verified, 1 unverified or failed.

| Reason | First difference found |
| --- | --- |
| `trace_unverified` | The replay's own trace failed verification. |
| `digest_mismatch` | The semantic digest differs. |
| `kpi_mismatch:<key>` | The named KPI scalar differs. |
| `runtime_mismatch` | Interpreter, platform or architecture facts differ. |
| `dependencies_mismatch` | The resolved dependency set differs. |
| `layout_mismatch` | The physical layout hash differs. |

A replay prefers the host of the original attempt. With `--worker NAME` it runs there, even if that host differs, and the result is then reported as unverified when the runtime facts differ. On the remote acceptance hosts (see [Remote workers](remote-workers.md)), a replay on the original host, strangelove, was verified, and the same request forced onto otacon (a different architecture) was unverified with `runtime_mismatch` while its digest and KPI scalars matched. Statistics keep using the original replication's scalars; a verified replay only stands in for the original where events are needed, such as side-by-side playback.

## Retention

Nothing is deleted automatically. The store under `.simulatte/` stays safe to delete; deleting it resets everything.

```bash
simulatte experiment delete RUN_ID      # a run that is not running
simulatte store prune --dry-run         # list what would go
simulatte store prune
```

`experiment delete` removes a run and the results that only that run references. `store prune` removes results that no experiment run, replay or view references and that are not among the newest 20 interactive results, plus snapshots that no stored request uses. Both update the index in one transaction and remove directories afterwards, so an interrupted prune leaves only unindexed directories that the next start removes. Studio offers the same delete action.

## Store migration

The 0.17 store uses schema 2 (slots, experiments, replications and replays). A store from 0.16 migrates automatically in one transaction when it is first opened, and keeps its request ids and results. A store written by a newer version is refused. To reset instead, delete `.simulatte/`. Protocol version 1.1 adds staging and provisioning messages for remote workers; local execution is unchanged.

## Windows

Local experiments, with two or more slots, cancellation and store migration, are supported on Windows and run in the native CI lane. Running remote workers from a Windows coordinator is untested in 0.17, and `simulatte workers check` says so. Windows is never a remote host.
