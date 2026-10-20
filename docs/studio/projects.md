# Projects, models and saved history

A project is a folder. Its authored definition lives in `pyproject.toml`; saved
execution state lives in `.simulatte/`. Multiple named model entrypoints share one
store and execution coordinator. Selection is separate from execution ownership.

## Register models

Use **Models → Register a Python model** to preview a registration before saving.
The source must be inside the project. Registration does not import or replace
that source. Model preparation is an explicit later action.

The equivalent configuration is:

```toml
[tool.simulatte.project]
schema = 1
id = "803b33d2-9549-4672-a291-439013f6af99"
default_model = "plant"

[tool.simulatte.models.plant]
id = "488785a1-f0d9-4f5d-8c42-d48baeb192c4"
name = "Production plant"
entrypoint = "models/plant.py:build"
layout = "layouts/plant.json"

[tool.simulatte.bundle]
include = ["models/*.py"]
layouts = ["layouts/plant.json"]
```

Generate fresh UUIDs for a new project/model, for example with
`python -c 'import uuid; print(uuid.uuid4())'`. Keep a model UUID when renaming its
key or label. Use a new UUID for an unrelated replacement. Duplicate IDs, escaping
paths and invalid defaults are rejected. Configuration previews preserve unrelated
TOML and comments; applying refuses a file changed since preview.

Git projects capture tracked membership and declared additions. Non-Git projects
must declare source membership with `bundle.include`; registration previews add the
chosen source, and starters declare their own files. Add imported helper modules
explicitly. Declare data with `@model(inputs=("data/rates.csv",))`. Data inputs and
layouts are captured separately; output/history and environment caches are excluded.
Studio does not silently initialize Git or include every file in the folder.

## Run and edit

**Prepare model** inspects metadata in a separate child. Preparing model B does not
revoke model A's active result. **Run** captures the selected model's current inputs.
Later edits cannot change those captured bytes. Busy admission preserves the draft;
explicit stop-and-run checks both the owner and the current input revision.

The model page chooses the **Recording** level: a full recording supports playback
and inspection; **KPIs only** keeps the numerical outputs without a trace, and the
run page says so ("Numerical outputs were kept without a recording. Analysis remains
available.").

Automatic runs are an explicit session option on a model page. Switching models
disables the previous model's automatic session. It cannot replace a manual run or
an experiment. Stale drafts require reloading current inputs before another run.

The model's **Layout** editor retains presentation/physical validation and revision
checks. Saving a physical layout does not implicitly run the workspace model: press
**Run** when ready. Presentation changes keep the recorded trace separate from the
view. See the [layout controls](../development/studio.md#parameter-and-layout-editing-016).

## Runs page

**Runs** (`#/runs`, or `#/runs/<model id>` with **Filter by model**) pages the saved
history 100 rows at a time; **Load more** appends older pages, and runs that finish
meanwhile are added on top without reordering what is loaded. Rows are grouped:

- **Interactive runs**, started explicitly from a model page.
- **Experiment and replay replications**, each linked to its experiment run.
- **Unassigned history**, saved before this project assigned runs to models.

**Search loaded runs** filters the loaded rows only, by name, status, notes, model
and identifier, and says how many of the loaded runs it shows and whether older runs
are not loaded yet. Nothing is selected automatically. Each row shows the seed, the
recording level, the stopping policy and the runtime in words; raw identifiers stay
in the diagnostic details.

**Edit notes** changes the run name, the notes and the **Important · protect from
pruning** flag in place. These are annotations, not changes to original provenance;
an edit that the server refuses is reverted and explained. The run page
(`#/run/<attempt>`) keeps the same annotations next to the recording, the recorded
KPIs and the provenance.

## History and portability

Important or referenced results are protected from pruning. Removing a registration
does not erase its history; the original model identity remains in the store.
Legacy results without a proven model association remain unassigned.

The store is at schema 5. Schema 3 added project/model associations without changing
existing request, attempt or result identities; schema 4 added analysis snapshots and
executions; schema 5 adds presentations, their immutable revisions and export jobs.
A writer makes a SQLite backup and manifest before upgrading an older store. A newer
unsupported schema is refused rather than modified. The
[storage page](storage.md#schema-versions-and-migration) lists what each schema holds.

**Do not treat `.simulatte/` as disposable.** It contains saved results, traces,
annotations, captured inputs, analysis snapshots and outputs, backups and durable
environment evidence. Back up the whole quiescent project, including this directory,
before moving or cleaning it. Stop Studio and other writers before copying.
Environment caches can require reconstruction after moving a virtualenv; saved
evidence and recordings remain useful. Use explicit store pruning for unwanted
history, respecting important/reference protection: results used by a saved analysis
or presentation, or by an export in progress, are kept until their last owner is
removed. No results are deleted automatically. See
[storage and portability](storage.md) for the layout, pruning, deletion, copying a
project and read-only recovery viewing, and [presentations](presentations.md) for
what saved presentations hold.
