# CLI reference

`simulatte` is one executable with eight user commands. Every option below was
checked against the output of `simulatte <command> --help` of this version. The
internal supervisor command (`simulatte worker --stdio`) is not part of the user
interface and is not described here.

| I want to | Command |
| --- | --- |
| Work in a project with the browser workspace | [`simulatte studio`](#studio) |
| Run a model once, headlessly | [`simulatte run`](#run) |
| Run many replications and compare configurations | [`simulatte experiment`](#experiment) |
| Check that a stored run reproduces | [`simulatte replay`](#replay) |
| Run a numerical analysis over saved results | [`simulatte analysis`](#analysis) |
| Remove unwanted saved history, or verify a project's files | [`simulatte store`](#store) |
| Configure and check remote hosts | [`simulatte workers`](#workers) |
| Open a recording file | [`simulatte view`](#view) |

Presentations have no command-line verb: create, present and export them in Studio
([Presentations](presentations.md)). The headless counterpart of the verified
original-seed recording is [`simulatte replay`](#replay).

Conventions shared by the commands:

- `--json` prints exactly one JSON document on standard output. Without it, results
  go to standard output or standard error as short text.
- `--project DIR` names the project directory that holds `.simulatte/`. It defaults to
  the current directory. `experiment`, `replay` and `store` need an existing store
  there and exit 2 with `No run store in DIR` otherwise.
- A command that changes the store takes the single writer lock. If Studio or another
  command owns it, the command reports `Another coordinator owns this .simulatte
  store`. Stop the owner and retry.
- Exit codes: `0` success, `1` the work failed, `2` invalid input or usage, `130`
  cancelled or interrupted. Each command lists the exceptions.
- `--help` on any command or subcommand prints its usage.

## Studio

```bash
simulatte studio                       # the current folder
simulatte studio studies/plant         # opens, or creates, this folder
simulatte studio plant.py:build        # register and open an existing entrypoint
simulatte studio [model] [--project PROJECT] [--port PORT] [--no-browser] [--layout LAYOUT]
                 [--param NAME=VALUE]... [--seed SEED]
                 [--horizon H | --arrivals N | --cutoff T] [--warmup W] [--safety-limit S]
                 [--level {full,kpi}] [--kpi KEY]... [--timeout SECONDS]
```

`model` is a folder, `MODEL.py` or `MODEL.py:build_name`. An explicit missing folder is
created; a missing `MODEL.py` or `MODEL.py:build_name` file is an error and creates nothing.
Opening a project never starts a simulation or imports a model.

| Option | Meaning |
| --- | --- |
| `--project PROJECT` | The project root, when the model lives inside a larger folder. The model must be inside it. |
| `--port PORT` | The loopback port, 0 to 65535. The default, 0, picks a free port. |
| `--no-browser` | Print the one-use access link instead of opening a browser. |
| `--layout`, `--param`, `--seed`, `--horizon`, `--arrivals`, `--cutoff`, `--warmup`, `--safety-limit`, `--level`, `--kpi`, `--timeout` | Initial run options for the opened model (the same meaning as for [`run`](#run)); they fill the form, they do not start a run. |

Studio listens on `127.0.0.1` only. The terminal accepts `link` for a fresh access
link; Ctrl-C closes Studio. The launcher can hand off to the project's own prepared
environment; if that environment cannot start, it opens the read-only recovery view
([Storage and portability](storage.md#read-only-recovery-viewing)). Exit code 2 reports
an invalid path, an unsupported store schema or missing Studio extras
(`pip install 'simulatte[studio]'`).

## Run

```bash
simulatte run MODEL.py[:build_name] [--project PROJECT] [--layout LAYOUT]
              [--param NAME=VALUE]... [--seed SEED]
              [--horizon H | --arrivals N | --cutoff T] [--warmup W] [--safety-limit S]
              [--level {full,kpi}] [--kpi KEY]... [--timeout SECONDS]
              [--output TRACE] [--json]
simulatte run --reproduce REQUEST_ID [--project PROJECT] [--timeout SECONDS] [--json]
```

Captures the model's source and inputs, runs it in a contained child and publishes the
result in the project's store. Model code runs only in that child.

| Option | Meaning |
| --- | --- |
| `model` | A Python file, optionally `:build_name`. Give exactly one of `model` and `--reproduce`. |
| `--project PROJECT` | The project root, which holds `.simulatte/`. By default the nearest folder above the model with a `pyproject.toml`, else the Git top level, else the model's folder. The model must be inside it. |
| `--layout LAYOUT` | Layout file (default `layout.json`). |
| `--param NAME=VALUE` | A model parameter, repeatable, parsed against the model's schema. |
| `--seed SEED` | Integer seed (default 0). |
| `--horizon H` | Stop at simulation time `H`. With `--warmup W`, observations before `W` are excluded. |
| `--arrivals N` or `--cutoff T` | Finite-population stopping. Needs a safety limit: `--safety-limit S`, or the model's own. |
| `--level {full,kpi}` | `full` records a trace (default); `kpi` keeps numerical outputs only. |
| `--kpi KEY` | Select a recorded KPI, repeatable. |
| `--timeout SECONDS` | Bound on preparation and execution. |
| `--output TRACE` | Also write the trace to this new file. An existing file is never overwritten. |
| `--json` | One JSON document: `status`, `request_id`, `attempt_id`, `path`, `trace`, `reproduced`; on failure `status` and `message`. |
| `--reproduce REQUEST_ID` | Rerun a stored request with its stored inputs. Execution options (`--param`, `--seed`, `--kpi`, `--level`, stopping options, `--layout`) are refused. |

`--horizon`, `--arrivals` and `--cutoff` are mutually exclusive; `--warmup` needs
horizon stopping and `--safety-limit` needs finite-population stopping. Exit codes:
`0` succeeded, `1` failed (including a failed reproduction), `2` invalid input or a
validation failure, `130` cancelled. Ctrl-C cancels the attempt.

## Experiment

```bash
simulatte experiment run FILE [--workers a,b] [--local-slots N] [--timeout SECONDS]
                              [--project PROJECT] [--json]
simulatte experiment status [RUN_ID] [--project PROJECT] [--json]
simulatte experiment results RUN_ID [--level L] [--baseline LABEL] [--bonferroni]
                              [--project PROJECT] [--json]
simulatte experiment resume RUN_ID [--local-slots N] [--timeout SECONDS]
                              [--project PROJECT] [--json]
simulatte experiment cancel RUN_ID [--project PROJECT] [--json]
simulatte experiment delete RUN_ID [--project PROJECT] [--json]
```

Runs the `experiment-v1` file `FILE` (an `*.experiment.json`) to completion; the
[experiments guide](../guides/experiments.md) defines the file and the statistics.

| Subcommand | Behavior |
| --- | --- |
| `run FILE` | Expands configurations by replications and runs them. `--workers` is a comma-separated list of worker names (default: the file's, otherwise local). `--local-slots` sets the local slots (default 4, at most the CPU count minus 1). `--timeout` bounds preparation and each replication. Warnings such as a private package index are printed to standard error. |
| `status [RUN_ID]` | One run's counts per configuration and replication state, or the newest runs. |
| `results RUN_ID` | Per-configuration t intervals. `--level` is the confidence level (default 0.95). `--baseline LABEL` adds paired differences against that configuration, paired by replication index. `--bonferroni` adjusts the level for the number of intervals shown. |
| `resume RUN_ID` | Continues a paused run with its saved settings. |
| `cancel RUN_ID` | Cancels a paused run for good. |
| `delete RUN_ID` | Removes the run and the results only that run referenced. Refused while the run is running or a saved analysis references it. |

Exit codes for `run` and `resume`: `0` completed, `1` failed, `130` cancelled or paused
(Ctrl-C pauses the run and prints how to resume it); `2` invalid input, including
preflight failures such as a missing lockfile (the JSON then carries a `code`). The
other subcommands exit `0`, `2` for invalid input (an unknown run id or no store) or
`1` for a failure.

## Replay

```bash
simulatte replay REQUEST_ID [--project PROJECT] [--worker WORKER] [--timeout SECONDS] [--json]
```

Re-executes a committed request at `full` level and compares it with the original:
trace verification, semantic digest, KPI scalars, resolved runtime, dependencies and
layout. `--worker` selects where to run (default: the original attempt's; `local` for
the local pool). `--timeout` bounds the replay.

Exit codes: `0` verified, `1` unverified or failed, `2` invalid input, `130`
cancelled. The JSON carries `verified` and, when it is false, `reason` (for example
`digest_mismatch` or `runtime_mismatch`). The replay is published either way.

## Analysis

```bash
simulatte analysis run KEY [--project PROJECT] [--json]
                           [--run ATTEMPT]... [--experiment RUN_ID]...
                           [--configuration N]... [--require-complete]
                           [--min-replications N] [--param NAME=VALUE]...
simulatte analysis rerun EXECUTION_ID [--project PROJECT] [--json]
simulatte analysis refresh EXECUTION_ID [--project PROJECT] [--json]
simulatte analysis show EXECUTION_ID [--project PROJECT] [--json]
simulatte analysis cancel EXECUTION_ID [--project PROJECT] [--json]
```

Runs a registered, reproducible numerical analysis; [Reproducible analysis](analysis.md)
describes definitions and outputs. Here `--project` defaults to the current directory.

| Subcommand | Behavior |
| --- | --- |
| `run KEY` | `KEY` is the analysis's key in `pyproject.toml`. Inputs are selected explicitly: `--run` (repeatable attempt ids) and `--experiment` (repeatable experiment run ids), optionally restricted by `--configuration` indices. `--require-complete` and `--min-replications N` set the completeness policy. `--param` supplies the analysis parameters. |
| `rerun EXECUTION_ID` | Recomputes on the same frozen snapshot as a new execution. |
| `refresh EXECUTION_ID` | Previews and admits the current results of the same selection as a new execution. |
| `show EXECUTION_ID` | Prints the execution, its details and its output manifest. |
| `cancel EXECUTION_ID` | Cancels it. If a live writer owns the store, a cancellation request is recorded for that writer. |

There is no implicit latest-run selection. Exit codes: `0` succeeded, `1` failed, `2`
invalid input, `130` cancelled. `--json` output uses the analytical tagged numeric
codec for `output` and `details`.

## Store

```bash
simulatte store prune [--dry-run] [--recordings] [--project PROJECT] [--json]
simulatte store check [--project PROJECT] [--json]
```

**`prune`** removes unreferenced results and snapshots; nothing is removed unless
nothing protects it. `--dry-run` reports what would be removed and changes nothing.
`--recordings` also drops presentation recordings no presentation adopted, beyond the
newest 20. It needs the store to itself. The JSON is `{"status", "dry_run", "results",
"snapshots"}` plus `"recordings"` with `--recordings`. On a dry run it adds
`"protected"`: a list of `{"result", "protected_by"}` with one owner chain per reason a
kept result stays (for example a presentation revision, then an analysis, then the
result). The text output ends with `; N results kept (--json names the owners keeping
each)`.

**`check`** verifies, read-only and without taking the writer lock, every indexed
trace, result artifact and published analysis artifact against its recorded SHA-256.
Exit `0` prints `verified N results and M analyses`; exit `1` lists the problems (text
on standard error, or `status: failed` with `problems` under `--json`), each with
`kind`, `id`, `name`, `path`, `sha256` and `problem` (`missing`, `unsafe` or
`changed`); exit `2` means there is no store at the project path. Use it after copying
a project.

The full rules are in [Storage and portability](storage.md#prune-unwanted-history).
`store` has only `prune` and `check`; other deletions are explicit per item
(`experiment delete`, Studio).

## Workers

```bash
simulatte workers list [--json]
simulatte workers check NAME [--json]
simulatte workers setup NAME [--install-uv] [--json]
simulatte workers prune NAME [--dry-run] [--json]
```

Workers are named in the user-level `workers.toml`
([Remote workers](../guides/remote-workers.md)); no command here edits that file or
sends credentials.

| Subcommand | Behavior |
| --- | --- |
| `list` | Name, host and slots of each configured worker. |
| `check NAME` | Connectivity, platform, shell, uv and cache; installs nothing. |
| `setup NAME` | Like `check`; with `--install-uv`, offers to install uv with the official installer after an explicit confirmation. |
| `prune NAME` | Removes cached blobs and environments no running attempt uses; `--dry-run` lists them. |

Exit codes: `check` and `setup`: `0` ready, `1` uv or the cache is missing, `2` unknown
worker or unreachable host. `prune`: `0` reachable, `2` otherwise. `list` exits `0`.

## View

```bash
simulatte view TRACE [--port PORT] [--no-browser]
```

Opens a recorded `.simtrace` file in the local, read-only viewer. It needs no project,
store or model. `--port` selects the loopback port (default: a free one) and
`--no-browser` prints the access link instead of opening a browser. This is distinct
from [`replay`](#replay), which re-executes a stored request.
