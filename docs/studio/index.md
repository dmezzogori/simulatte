# Studio

Studio is an optional local workspace for Python simulation projects. Open a
folder, register named models, prepare its environment and choose when to run.
Opening a project never starts a simulation or imports a model. From one project you
can run individual models, compare experiments across replications, analyse their
populations and turn a chosen run into a presentation with its supporting evidence.

## Install and launch

```bash
pip install 'simulatte[studio]'
simulatte studio                 # current folder
simulatte studio studies/plant   # opens or creates this folder
simulatte studio plant.py:build  # register an existing Python entrypoint
```

Studio listens on loopback only. `--no-browser` prints a one-use access link;
`--port 8765` selects a port. Keep the access link private. Ctrl-C closes Studio.
An interactive terminal accepts `link` to issue a new browser link. The
[CLI reference](cli.md#studio) lists every option.

### Launcher and project environment

Two Python environments take part, and they have different jobs.

- The **launcher** is the Simulatte you installed with `pip install 'simulatte[studio]'`
  (for example in a tool environment or a virtual environment). It opens the folder,
  reads the saved history and serves the first page. It does not import your model.
- The **project environment** is the immutable environment Studio prepares from the
  project's `pyproject.toml` and `uv.lock` with [uv](https://docs.astral.sh/uv/). Model
  inspection, runs, experiments and analyses run in it, in contained child processes.
  It must provide a Simulatte release compatible with the launcher.

After **Use environment and restart**, the launcher hands the project to a Studio
started from the project environment, so the server, assets and execution runtime match
the project. If that environment is missing, incompatible or fails to start, the
launcher opens a read-only recovery view of the saved work instead
([Environments and recovery](environments.md)). The environment directories are
rebuildable; the saved history is not ([Storage and portability](storage.md)).

## First run

1. In an empty folder, choose **Production** or **Intralogistics**. Review the
   proposed files and apply them. Existing files are never overwritten by a starter.
   Alternatively, choose **Register an existing Python model**.
2. Choose **Prepare environment**. Review and apply the proposed lockfile, then
   prepare again. Studio installs a separate immutable environment. Choose
   **Use environment and restart** to open Studio in that runtime.
3. Open a model and choose **Prepare model**. Inspection happens in a separate
   child process. Review its parameters, seed, stopping policy and recording level.
4. Press **Run**. The run page shows progress and, for full recordings, the existing
   scene, entity inspector, recorded KPIs and event filters.
5. Give useful runs a name or notes. Close Studio and reopen the folder: **Runs**
   retains the saved history, without starting another run.

The production starter also runs as ordinary Python:

```bash
uv run python models/production.py
uv run simulatte run models/production.py:build --seed 29
```

The Python API, `Runner`, headless experiments and `simulatte view run.simtrace`
remain available independently of the workspace. Model code does not import Studio.
The bare-Python tutorials are unchanged; the same code runs in Studio.

## From a first run to a presentation

Follow the pages in this order. The runnable `examples/studio_project` (a production
and a fleet model with measurements, analyses and data) is enough for every step.

1. **First run**, above: create or open a project, prepare the environment, run.
2. [Projects, models and saved history](projects.md): several models, notes and what
   is kept.
3. [Numerical measurements](measurements.md): declare the populations to analyse.
4. [Experiments and comparisons](experiments.md): replications, intervals, paired
   differences.
5. [Reproducible analysis](analysis.md): a saved analysis over the experiment's
   replications.
6. [Presentations](presentations.md): record a run of a chosen configuration and seed,
   save a view with evidence, present it and export a video or offline HTML.
7. [Storage and portability](storage.md) and the [CLI reference](cli.md): clean up,
   move a project, repair dependencies, script the headless commands.

## Navigation

The sidebar owns navigation. Every page has a stable address, so a bookmark or a
reload reopens the same identity; malformed addresses open the overview.

| Page | Address | Shows |
| --- | --- | --- |
| Overview | `#/overview` | Model, run and environment counts and the next step. |
| Models | `#/models`, `#/model/<id>` | Registrations; a model's inputs, layout, recording level and Run. |
| Runs | `#/runs`, `#/runs/<model id>`, `#/run/<attempt>` | Saved history, grouped and searchable; one run's recording and provenance. |
| Experiments | `#/experiments`, `#/experiments/<run id>`, `#/compare/<run id>[:<configuration>],…` | Definitions, runs, replication progress, results and numerical comparisons. |
| Analyses | `#/analyses`, `#/analyses/<execution id>`, `#/analyses/new/<key>` | Saved analyses, one execution with its outputs, admission of a new one. |
| Presentations | `#/presentations`, `#/presentations/<id>`, `#/presentations/<id>/present`, `#/presentations/new/<attempt>` | Saved presentations, the recording and diff form, the editor with evidence, presentation mode and export. |
| Settings | `#/settings` | Environment ownership and project setup. |

The breadcrumb names the project, the section and the selected identity. Finished
work raises a completion notice (an interactive run, an experiment or an analysis)
with an **Open →** link; a notice never changes the page you are on, never touches a
draft, and **Dismiss** or Escape removes it. At most five notices are kept.

Selecting another model preserves its draft and does not start or cancel a run.
If the interactive slot is occupied, Studio identifies the owner. Inspect that run,
wait, or explicitly choose **Stop and run this model**. Cancellation and admission
recheck the owner, so another tab cannot accidentally replace a newer run.

## Keyboard use and page states

Every primary action is a button or a link. **Skip to content** is the first Tab
stop, focus is always visible, and controls that are disabled say why next to
them. Reduced-motion settings disable transitions and spinners' animation.

Pages use designed states instead of blank panels or browser errors:

- loading (a labelled spinner),
- empty (what is missing and the next action),
- error (classified as a model error, an incomplete environment, missing data or a
  connection problem, with a hint and collapsible diagnostic details; raw hashes and
  tracebacks stay in the details),
- stale (shown data may be out of date, with the reason),
- disconnected ("Reconnecting. Drafts are retained; commands wait for authoritative
  state."),
- authentication expired ("Open a fresh link from the serving terminal. A restarted
  Studio uses a new session.").

See [projects and history](projects.md), [experiments and comparisons](experiments.md),
[reproducible analysis](analysis.md), [presentations](presentations.md),
[storage and portability](storage.md), [environments and recovery](environments.md),
the [CLI reference](cli.md), and the
[recording controls and export reference](../development/studio.md).
