# Studio

Studio is an optional local workspace for Python simulation projects. Open a
folder, register named models, prepare its environment and choose when to run.
Opening a project never starts a simulation or imports a model.

```bash
pip install 'simulatte[studio]'
simulatte studio                 # current folder
simulatte studio studies/plant   # opens or creates this folder
simulatte studio plant.py:build  # register an existing Python entrypoint
```

Studio listens on loopback only. `--no-browser` prints a one-use access link;
`--port 8765` selects a port. Keep the access link private. Ctrl-C closes Studio.
An interactive terminal accepts `link` to issue a new browser link.

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
| Presentations | `#/presentations` | Reserved for a later package; export stays on the run page. |
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
[reproducible analysis](analysis.md), [environments and recovery](environments.md),
and the [recording controls and export reference](../development/studio.md).
