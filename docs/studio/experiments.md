# Experiments and comparisons in Studio

**Experiments** runs the experiments of the [experiments guide](../guides/experiments.md)
inside a project. Every number on these pages comes from the server's replication-level
statistics; the browser computes nothing itself. Experiment runs fence on their run id and
the store, not on the project revision: editing a model's parameters, layout or source
supersedes the interactive run but never a running experiment.

## Define an experiment

Choose the **Experiment model** in the Definitions card. The model must be prepared
(**Prepare model** on its page) so its parameter schema is known. **Define a new
experiment** opens the form:

- Name and file. The file is saved inside the project; **Replace an existing file**
  must be checked to overwrite one.
- Replications and base seed. Seed `base_seed + i` is shared by every configuration of
  replication `i`.
- Configurations as a grid (comma-separated values per parameter) or an explicit list.
- Layout variants (`label=file` per line), stopping policy, recording level, KPI keys
  and workers.

The form validates against the model's parameter schema. Only files that belong to the
captured project are listed after a reload: Git-tracked files, or files matched by
`[tool.simulatte.bundle] include` (for example `include = ["*.experiment.json"]`).

## Start, cancel, resume, delete

**Start** next to a definition sends `experiment.start` with that file. **Execution
settings** adds a worker selection (`local` plus the names of the user-level
`workers.toml`) and a recording level override; empty settings keep the definition's
level and run on the local slots. Remote workers show name, host and slot count, never
credentials, and each slot's state: `idle`, `backoff` after an unreachable worker, or the
status of its last attempt.

The runs table lists the newest runs first with their state and replication counts;
**Load older experiment runs** pages through the rest, and a bookmarked run opens from its
address even when it is no longer among the newest. Each run offers exactly the controls its
state allows, and the footnote says what each sends:

| State | Controls | What happens |
| --- | --- | --- |
| `running` | Cancel | `experiment.cancel` stops the run for good. There is no pause. |
| `paused` | Resume, Cancel, Delete | A run is paused only because Studio stopped while it ran; `experiment.resume` continues it with its saved worker selection. |
| `completed`, `failed`, `cancelled` | Start again, Delete | `experiment.start` with the saved definition reuses committed replications by content; `experiment.delete` asks for confirmation and removes the results only that run referenced. |

A restart pauses running experiments; nothing resumes on its own. The same definition
on unchanged inputs returns the existing run instead of starting a second one.

## Replication progress and failures

Opening a run (`#/experiments/<run_id>`) shows counts per configuration that update as
replications finish, and the replication list filtered by status or configuration.
`lost` attempts are dispatched again at most twice; `failed` replications are never
retried. A replication with a committed recording opens on its run page. A finished
experiment raises a completion notice with an **Open →** link; the notice never changes
the page you are on.

When a model's source revision moves past the revision the page saw, its definitions
carry a **stale** badge: new runs capture the current source, saved runs keep the inputs
they captured. **Acknowledge** clears the badge.

## Results table conventions

The results table lists configuration × KPI rows with `n`, mean, half-width and a state:

- `ok`: a t interval over the succeeded replications of that configuration.
- `degenerate`: all replications equal, zero-width interval.
- `insufficient`: fewer than two replications, no half-width.

**Level** sets the confidence level (0.8–0.999). **Bonferroni** divides it across the
family of rows; the label switches from "pointwise 95 % t intervals, no
multiple-comparison correction" to "Bonferroni-adjusted (m = …, effective level …)".
**Baseline** adds paired differences, configuration minus baseline, paired by
replication index and labelled as their own family. **Missing pairs** lists replications
without a result and the unpaired indices of each comparison; they are excluded from
every interval and difference. A **mixed runtime** badge marks a configuration whose
replications resolved different dependency sets. Within-run observations are never
pooled across runs here: inference stays replication-level.

## Comparison page

**Compare numerically →** opens `#/compare/<run_id>[:<configuration>],…`, a bookmarkable
comparison of up to eight experiment runs or single configurations. Each run gets a card
with its configuration counts, runtime consistency, the interval convention and the
results table; level, Bonferroni and baseline apply to all cards. Entries that name no
experiment run are ignored with a notice, and a deleted run keeps its place in the
address with an explanation. Read-only sessions show saved results without recomputing.

## Playback compare

**Compare recordings side by side** stays on the Experiments page as a tool for selected
full recordings: two recordings on one clock, chosen from interactive results, committed
replications and replays. A KPI-only replication offers **Replay at full level**; the
replay is labelled verified or unverified with its reason. The [recording
reference](../development/studio.md#replays-and-compare-playback) describes the controls.
