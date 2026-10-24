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

## Studies and results

The sidebar lists **studies**: an experiment definition read through its runs, newest
first. A study is derived from the runs' names; nothing extra is stored. Opening a study
shows the results of its newest run; under it, **Analyses** lists the saved analyses whose
inputs include one of its runs. **All experiments** keeps the definitions, every run and the
execution settings.

A run's results page leads with one block per KPI, drawn as a small forest plot:

- Each configuration is a row with its estimate, its t interval in brackets, `n`, and the
  interval on an axis the KPI's configurations share, so overlap is visible.
- Squares under each configuration count its replications: filled when they succeeded,
  struck through when they failed.
- Values print to the precision their interval supports (two significant digits of the
  half-width). **Full digits** shows every digit the server returned.
- A note names the special cases: `degenerate` (all replications equal, no interval),
  `insufficient` (fewer than two replications, no interval) and an interval wider than the
  estimate.
- KPIs where no configuration has an interval are listed together as one sentence each.

**Baseline** defaults to the first configuration of the definition; choose another one or
**None**. Each block then adds the differences, configuration minus baseline, paired by
replication index and labelled as their own family, drawn around a zero line. A filled
diamond means the difference interval excludes zero, a hollow one that it includes zero.
The colour says nothing about which configuration is better: KPIs do not declare a
direction. The unpaired replications of each difference are named under it.

**Level** sets the confidence level (0.8–0.999). **Bonferroni correction** divides it across
the family; the label switches from "pointwise 95 % t intervals, no multiple-comparison
correction" to "Bonferroni-adjusted (m = …, effective level …)". **Matrix** shows the same
estimates as a table, configurations by KPI, sortable by any KPI. Above eight
configurations the figure shows one slice of the grid: a select per parameter fixes its
value or shows all of them, and the configurations outside the slice stay on each axis as
grey ticks.

Replications without a result are named under **Replications** and excluded from every
interval and difference. A **mixed runtime** badge marks a configuration whose replications
resolved different dependency sets. Within-run observations are never pooled across runs:
inference stays replication-level.

## Comparison page

**Compare with other runs** opens `#/compare/<run_id>[:<configuration>],…`, a bookmarkable
comparison of up to eight experiment runs or single configurations. Their configurations
are rows of the same KPI blocks, on shared axes, each labelled with its run; differences stay
within a run. Level, Bonferroni and baseline apply to every run. Entries that name no
experiment run are ignored with a notice, and a deleted run keeps its place in the address
with an explanation. Read-only sessions show saved results without recomputing.

## Playback compare

**Compare recordings side by side** stays on the Experiments page as a tool for selected
full recordings: two recordings on one clock, chosen from interactive results, committed
replications and replays. A KPI-only replication offers **Replay at full level**; the
replay is labelled verified or unverified with its reason. The [recording
reference](../development/studio.md#replays-and-compare-playback) describes the controls.

## Present a configuration

Experiments are numerical. To show a configuration as an animation, record a full run
of it on the **Presentations** page: with the replication's own seed it is a verified
replay, with another seed it is a new recording that is labelled as such. The stopping
policy, warm-up and measurements of the experiment are kept. See
[Presentations](presentations.md#an-experiment-is-numerical-a-presentation-needs-a-full-recording).
