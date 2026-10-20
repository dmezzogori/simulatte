# Storage and portability

A Studio project keeps three kinds of data, and each is cleaned, copied and repaired
differently.

| Kind | Where | Examples | Can it be rebuilt? |
| --- | --- | --- | --- |
| Authored material | Ordinary files in the project | `pyproject.toml`, model sources, layouts, data, `*.experiment.json`, analysis sources | From version control or your own backup |
| Durable study history | `.simulatte/` | Runs and traces, experiment results, captured sources and inputs, analyses and their outputs, annotations, presentations, environment evidence | No. Deleting it destroys the history. |
| Rebuildable state | Inside `.simulatte/` and elsewhere | Environment generations, derived table indexes, temporary staging, remote worker caches, browser export staging | Yes, from the durable history and the lockfile |

**Never delete `.simulatte/` to fix a runtime problem or to free space.** Cache
cleanup, pruning and deletion are separate, explicit operations described below.
Nothing is ever deleted automatically.

## Layout of `.simulatte/`

| Path | Holds | Kind |
| --- | --- | --- |
| `index.sqlite` (with `-wal` and `-shm` while a writer is open) | The store: requests, attempts, results, experiments, analyses, presentations, annotations, environment evidence and the reference table | Durable |
| `coordinator.lock` | The single-writer lock | Operational |
| `results/<attempt>/` | A committed run: its trace, artifacts and datasets | Durable |
| `snapshots/<hash>/` | Captured model sources, input data and runtime bundles, addressed by content | Durable |
| `documents/<sha256>.msgpack` | Reference documents that requests and preparations point to: runtime evidence, layout, model metadata and requests, addressed by content | Durable |
| `analysis-results/<execution>/` | The verified outputs of a succeeded analysis | Durable |
| `backups/schema-<n>-<id>/` | The pre-migration copy of `index.sqlite` and its `manifest.json` (created by the first migration) | Durable safety copy |
| `cache/environments/<generation>/` | A managed environment generation | Rebuildable |
| `analysis-cache/` | Derived sort and filter indexes of analysis tables | Rebuildable |
| `staging/`, `analysis-staging/`, `analysis-control/` | Work in progress and cancellation requests | Temporary; emptied when a writer opens the store |

One writer owns the store at a time: Studio, or one CLI command that mutates it.
A second one reports the owner instead of competing (the CLI says
`Another coordinator owns this .simulatte store`; a second Studio opens the
read-only recovery view). There is no background daemon.

Presentations are stored as immutable revisions in `index.sqlite`. An export is a job
record (target, options, state, and the name, size and SHA-256 the browser reported);
the exported file itself stays wherever the browser saved it and is not kept in the
store.

## Schema versions and migration

The store carries a schema version. Opening it with a writer migrates forward in one
transaction and never changes a request, attempt or result identity.

| Schema | Adds |
| --- | --- |
| 1 | The original single-attempt run store |
| 2 | Execution slots, experiments with their configurations and replications, and replays |
| 3 | Project and model associations, history annotations, environment generations and the reference table |
| 4 | Analysis definitions, frozen input snapshots, executions, outputs and annotations |
| 5 | Presentations, immutable presentation revisions and export jobs |

Before any migration, the writer copies the database to
`.simulatte/backups/schema-<old version>-<id>/index.sqlite` and writes a
`manifest.json` with the old schema, the SQLite version and the copy's SHA-256. If the
copy cannot be made, the store is not touched. A store written by a newer Simulatte is
refused with a message rather than modified; open it with that version or newer.

Keep the backup until you are satisfied with the migrated project; it is a complete
SQLite copy. Restoring one over `index.sqlite` is a manual file operation that this
documentation has not exercised end to end: stop every writer, back up the whole
folder first, and do not mix a restored index with result folders created after the
migration.

The read-only reader used by the launcher's recovery view and by `StoreReader`
opens schemas 2 to 5 and performs no migration. A schema 1 store needs a writer
open once (which migrates it) before it can be read this way.

## References and what protects results

Saved work refers to the evidence it needs through a reference table. Cleanup follows
the whole chain, including evidence shared by several owners, before it removes
anything. A result is kept while any of these holds:

- an experiment run lists it as a replication, or a replay names it as the original or
  the replay;
- a saved layout view names it, or it is one of the newest 20 interactive results;
- its run is marked **Important · protect from pruning**;
- a saved or waiting analysis execution references it (with its experiments,
  attempts, requests, definition revision, input snapshot and environment
  generation);
- a presentation revision references its recording (result and request) and the
  analysis executions and snapshots of its evidence;
- an export that is waiting or running holds the same references, plus its
  revision, until it finishes;
- a presentation recording that no presentation has adopted yet references itself
  and the original result;
- active work (a run, inspection or analysis) pins the environment generation it
  uses.

Admitting an analysis freezes and protects its inputs in one step, before it can wait
or start; a result cannot be pruned between selection and use. A partial analysis
protects exactly the replications it admitted. A reference survives until its last
owner is removed: if two analyses and a presentation use one result, deleting any
one or two of them leaves it protected.

## Prune unwanted history

```bash
simulatte store prune --dry-run --project ./study
simulatte store prune --project ./study
simulatte store prune --recordings --dry-run --project ./study
```

`store prune` needs the store to itself: close Studio and other commands first. It
removes:

- results nothing protects, with their `results/<attempt>/` folders;
- snapshot folders that no request, captured preparation or reference names;
- analysis previews no execution admitted, with their unused input snapshots;
- the derived `analysis-cache/` (readers rebuild it on demand);
- the revisions and finished export jobs of presentations that were deleted;
- with `--recordings`, presentation recordings that no presentation or export
  adopted, except the newest 20, and the results that only they kept.

It never removes environments, backups, authored files or anything referenced by a
saved analysis, presentation or active export. The index commits first and folders
are removed afterwards; an interrupted prune leaves only unindexed folders, which the
next writer removes when it opens the store.

**`--dry-run` computes the same removal and changes nothing, and explains what stays.**
Its text form is one line (`would remove N results and M snapshots`, or `would remove
N results, M snapshots and K recordings` with `--recordings`) followed by `; N results
kept (--json names the owners keeping each)`. With `--json` the lists are exact, and
`protected` names, for every kept result, each chain of owners that keeps it:

```json
{"status":"succeeded","dry_run":true,
 "results":["<attempt>"],"snapshots":["<hash>"],"recordings":["<attempt>"],
 "protected":[{"result":"<attempt>",
   "protected_by":[[{"kind":"presentation-revision","id":"<presentation>:2"},
                    {"kind":"analysis","id":"<execution>"}]]}]}
```

(`recordings` appears only with `--recordings`; `protected` appears only on a dry run.)
Each chain starts at the owner and ends at the result's own reference. The kinds you
will meet are `experiment` followed by `replication` (`<run>/<configuration>/<index>`),
`replay` (and the `request` it reproduces), `view`, `recent-interactive`,
`important-annotation`, and saved references such as `presentation-revision`,
`export-job` and `analysis`. A result shared by several owners lists one chain per
owner; it stays until the last of them is removed.

### Presentation recordings

A presentation recording holds a reference to its own result and to the result it was
cloned from until `store prune --recordings` drops it. That option drops only recordings
no presentation revision or export adopted, beyond the newest 20, and an ordinary prune
afterwards frees their results. An adopted recording is never dropped; the revision
that adopted it protects the same results. Without `--recordings`, no recording is
dropped.

## Explicit deletion

Deletion is always an explicit request about one item, and it is refused while
something still needs the item. Remove dependents first, then the item, then prune.

| Item | How | Refused while |
| --- | --- | --- |
| Presentation | **Delete** on the presentation (asks for confirmation) | One of its exports is waiting or running, or another saved item references the presentation or one of its revisions (`Presentation is referenced; delete that owner first`) |
| Analysis execution | `AnalysisStore.delete(execution_id)` in Python (no CLI or Studio control) | It is waiting, running or finalizing (cancel it first), or a presentation references it |
| Experiment run | `simulatte experiment delete RUN_ID`, or **Delete** on the run in Studio | It is running (cancel it first), or a saved analysis references it |
| Interactive run | No delete control. Clear **Important**; once it is not among the newest 20 interactive results and nothing references it, `store prune` removes it | Not applicable: prune keeps it while anything protects it |

Deleting releases that item's references and removes only what nothing else holds.
A deleted presentation keeps its revisions readable for its finished exports until the
next prune. So, to remove evidence shared by several owners, delete in dependency
order (presentations, then analyses, then the experiment run), then run
`simulatte store prune`. Each refusal names the owner kind to delete first.

## Cache cleanup

Cache cleanup never touches durable results.

- **Derived indexes.** `analysis-cache/` is removed by `store prune`.
- **Environment generations.** Studio keeps each generation under
  `.simulatte/cache/environments/<generation>/` and its evidence in the store.
  `EnvironmentPool.cleanup(generation_id)` in the project API removes the directory
  of a generation that is not selected, has no active owner and is not referenced; it
  refuses otherwise, and refuses existing interpreters, which Studio does not manage.
  The evidence stays, so a later reconstruction is still possible if the original
  Python and packages are available again. See [Environments and recovery](environments.md).
- **Remote worker caches.** `simulatte workers prune NAME` removes staged blobs and
  environments no running attempt uses; see the [CLI reference](cli.md#workers).
- **Browser export staging.** Temporary copies of exports live in the browser origin's
  storage, not in the project. See the [recording reference](../development/studio.md#share-a-recording).

## Move or copy a project

1. **Make the project quiescent.** Stop Studio (Ctrl-C) and any running
   `simulatte` command that uses the project. A running writer can leave results
   half-written; do not copy a live project.
2. **Copy the whole folder**, including `.simulatte/`. If `index.sqlite-wal` is
   present, copy it with `index.sqlite` and `index.sqlite-shm`; better, open and
   close the project once with Studio so SQLite checkpoints them.
3. **Verify the copy** with `simulatte store check --project PATH` (below).
4. **Open the copy** with `simulatte studio PATH`. Ids of runs, requests,
   attempts, analyses and presentation revisions are unchanged, and every
   persisted reference is either a relative path or a content identifier. A project
   that moves keeps its history, and the original location does not need to exist.
5. **Rebuild what is machine-specific.** Environment directories are not
   portable between locations or machines. If the selected environment cannot start
   on the new machine, Studio opens the read-only recovery view (below). Prepare the
   environment again from **Settings** (a new generation; history is not rewritten),
   or reconstruct the original from its evidence.

The interpreter path of an **existing** environment is a machine-specific fact
recorded as evidence. On another machine that path usually does not exist, so the
launcher opens the recovery view until you select an environment that works there.
Saved work does not depend on it.

A copy on the same machine records the same absolute path, which still points into the
original folder (for example `<original>/.venv/bin/python`). Studio does not run a copy
from another project's environment: when the selected existing interpreter lies in a
different folder that holds a `.simulatte/` store, the copy opens in the recovery view
with `Project interpreter is unavailable`, while the original keeps working. Select or
prepare an environment for the copy, for example its own `.venv`. An interpreter inside
the copied folder, a standalone Python, or the environment of a plain Python project
without a `.simulatte/` store (a shared development environment) is used as before.

An artifact is identified by its recorded path, length and SHA-256. After copying,
verify the project without a writer:

```bash
simulatte store check --project ./study-copy
```

`store check` opens the store read-only and checks every indexed result trace, result
artifact and published analysis artifact against its recorded SHA-256. It prints
`verified N results and M analyses` and exits 0, or lists every problem and exits 1.
With `--json`, `problems` holds `{kind, id, name, path, sha256, problem}` entries,
where `problem` is `missing` (the file is absent), `unsafe` (a link or a path that
escapes the result) or `changed` (the bytes do not match). A file with the right name
and other bytes is `changed`; it is never adopted in place of the recorded one. Copy
the named files again from the source rather than editing the store. Exit code 2 means
there is no store at the path.

### Read-only recovery viewing

If project-local Studio cannot start (missing interpreter, incompatible runtime,
broken dependencies), the launcher opens a read-only recovery view with its own
compatible server and assets. It does not import project code, prepare an
environment, migrate the store or take writer ownership, and execution and editing
are disabled. Saved recordings, KPIs, analysis outputs and presentations stay
readable. The same view opens for a copied project whose environment is not usable
yet, and for a second Studio on a store another process owns.

### Offline viewing versus execution verification

These two are different promises.

| | Offline viewing | Execution and verification |
| --- | --- | --- |
| Needs | A compatible Simulatte with Studio, the copied project, nothing else | The captured source, inputs and a runtime matching the captured one |
| Works on another machine or architecture | Yes | Not guaranteed |
| Covers | Opening runs, traces, KPIs, analysis outputs, annotations, presentations and their exports | New runs, replays, presentation recordings, new analyses |
| Depends on network, model code, packages | No | Packages, and the model code of the captured snapshot |

Opening a result never re-executes anything. Running or replaying on another
architecture re-executes the model in a new environment, so the numerical outcome can
differ. A replay is labelled `verified` only when its trace, semantic digest, KPI
scalars, resolved runtime, dependencies and layout all equal the original's; otherwise
it is `unverified` with the first mismatch (`digest_mismatch`, `kpi_mismatch:<key>`,
`runtime_mismatch`, `dependencies_mismatch`, `layout_mismatch`, `trace_unverified`).
A replay or a presentation recording never substitutes another runtime. A different
trusted Simulatte runtime is refused before an attempt is scheduled (`Requested
runtime differs from this installation; no automatic provisioning`). Any other
difference in the installed runtime is found by the attempt itself, which fails with
`Installed runtime changed after preparation` (for installation evidence `Installed
payload changed after preparation` or `Local artifact is not bound to verified
captured source`); `simulatte replay` then exits 1.
Reconstructing a historical environment is refused on a different platform or
architecture with `Captured interpreter platform is unavailable`. Running with
updated dependencies is a new run with its own provenance, never a verification of
the old one. Whether a particular model reproduces bit for bit across architectures
has not been measured for the example project, and this documentation does not
claim it.

## Dependency repair

Missing optional packages, a broken environment or a moved project do not stop you
from reading saved work. To repair execution:

1. Open **Settings** (or the first-run card), choose **Preview dependency update** if
   the lockfile must change, apply the preview, then **Prepare environment**. The new
   generation is selected only after it validates, and **Use environment and restart**
   activates it when no work is active.
2. To keep the original dependency set, restore the interpreter at its recorded path
   or reconstruct the generation from its retained evidence (see
   [Environments and recovery](environments.md#saved-work-recovery)). If evidence is
   insufficient, reproduction is reported unavailable; Studio never presents current
   dependencies as the original.
3. Rerun anything you want to trust under the new environment as a new execution.

## Workers

Remote workers are personal configuration, not project data. They live in
`~/.config/simulatte/workers.toml` (or the file named by `SIMULATTE_WORKERS`), contain
no credentials, and are not part of a copied project. On a new machine, recreate the
file and run `simulatte workers check NAME`. A replay runs on the original attempt's
worker unless `--worker` names another; use `--worker local` when the original worker
is not configured. A host's cache is rebuilt by staging on the next use, and is safe
to delete or prune. The [remote workers guide](../guides/remote-workers.md) describes
the host requirements and cache.

## What this page has and has not verified

Written from the implementation and checked against the commands' `--help` output:
the layout, schema list, backup format, protection rules, prune and deletion
refusals, the dry-run and `store check` output, and, through the project portability
tests, that a quiescent copy reads, rebuilds and prunes with its source location
removed and that a partial transfer is diagnosed by file and hash.

Not verified here:

- restoring a pre-migration backup over a live index;
- whether any particular model gives identical numbers on a different CPU
  architecture, and how a rebuilt environment behaves on one;
- browser behavior of exports on any particular platform.
