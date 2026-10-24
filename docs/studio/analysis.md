# Reproducible analysis

An analysis is a named function over immutable simulation evidence. Its code,
parameters, environment, admitted inputs and outputs have separate identities.
It executes in a one-use local child, independently of simulation slots.

## Define and register an analysis

```python
from simulatte.analysis import analysis, DatasetRequirement
from simulatte.analysis.reports import summarize_population
from simulatte.model import ParameterSchema

@analysis(parameters=ParameterSchema(()), requires=(
    DatasetRequirement("job_outcomes", ("family", "lateness")),
))
def lateness(context, parameters):
    rows = (row for row in context.rows("job_outcomes") if row["included"])
    context.emit("lateness_by_family", summarize_population(
        rows, value="lateness", group_by=("family",), unit="hours",
        population="completed included jobs",
    ))
```

Register it in `pyproject.toml` with a stable UUID:

```toml
[tool.simulatte.analyses.lateness]
id = "e64ca951-1b69-47a0-b1c5-7cfb91d90755"
name = "Lateness by family"
entrypoint = "analysis/reports.py:lateness"
```

Include the source and data in the project's bundle. Inspection imports project
code only in a bounded child sharing the project's inspection capacity. It does
not import project code into the coordinator. The example's second definition
uses `fleet_utilization(context.rows("agv_durations"))` to emit the separately
labelled mean-of-fractions and ratio-of-durations measures.

## Headless commands

Select evidence explicitly; there is no implicit latest-run selection:

```bash
simulatte analysis run lateness --project ./study --run ATTEMPT_ID --json
simulatte analysis run lateness --project ./study --experiment EXPERIMENT_RUN_ID --json
simulatte analysis run lateness --project ./study --experiment EXPERIMENT_RUN_ID \
  --configuration 0 --min-replications 8 --require-complete --json
simulatte analysis rerun EXECUTION_ID --project ./study --json
simulatte analysis refresh EXECUTION_ID --project ./study --json
simulatte analysis show EXECUTION_ID --project ./study --json
simulatte analysis cancel EXECUTION_ID --project ./study --json
```

`--run` and `--experiment` are repeatable, and `--param NAME=VALUE` supplies
validated analysis parameters. All subcommands default `--project` to cwd.
Each `--json` response is one JSON document; its `output` and `details` fields use
the analytical tagged numeric codec. Success exits 0, failed execution 1,
invalid input 2 and cancelled execution 130. A cancellation request to a live
writer returns `cancellation_requested`; the owning writer fences publication.
Keyboard interruption also cancels and cleans up the child.

The selected project environment is used when present. Without a selected
generation, the current interpreter is captured as an existing environment;
analysis does not silently install packages or change environments. Missing
optional dependencies appear in bounded inspection/execution diagnostics. The
inspection and execution children import `simulatte.analysis.child` from that
environment, so it must provide a Simulatte release with the analysis API; a
project pinned to an older release fails at inspection with a module-not-found
diagnostic rather than a capability message.

## In Studio

**Analyses** (`#/analyses`) lists the definitions registered in `pyproject.toml`,
each with a **New analysis** button, and the saved executions newest first with
their state, their input counts ("8 of 24 replications · partial") and the last
annotation. Every saved execution has a stable address, `#/analyses/<execution id>`.

### Admit a new analysis

`#/analyses/new/<key>` selects inputs explicitly; nothing is selected by default and
there is no latest-run shortcut. Tick experiment runs (the newest 20 first; **Load
older experiment runs** pages the rest, with their status and replication counts),
optionally restrict them to configurations, tick
completed runs with results, and set the completeness policy (**Require complete
inputs**, **Minimum replications**). **Preview inputs** takes a dry-run snapshot and
shows the available, planned and missing replication counts, the snapshot identity
and the definition. A partial preview is labelled as such: later replications never
join that analysis; a refresh creates a new preview and a new execution.

When the selection does not satisfy the definition, the page shows a missing-data
screen instead of a preview: the dataset, the required fields, the valid and total
replication counts and each unavailable replication with its reason, with a link to
**Define a revised experiment**. Studio never substitutes synthetic values.

The preview returns the definition's parameter schema as a form; fill the required
parameters, name the execution if you like, and **Admit and run**. Admission freezes
exactly the previewed inputs and submits the execution. Its page follows the state
(`waiting`, `running`, `finalizing`, then `succeeded`, `failed`, `cancelled` or
`lost`) and a completion notice appears wherever you are.

### A saved execution

The execution page leads with its outputs. Below them, **How these numbers were made**
shows the input snapshot (counts, the selected experiment runs and runs named by
experiment and date, the configurations, the completeness policy), the definition and its
parameters, and, folded, the derived provenance with the identifiers (execution, input
snapshot, definition revision, source hash, environment generation). Annotations, an
append-only list of revisions, close the page. Outputs are shown only for a succeeded execution; a failed, cancelled,
running or lost execution shows its state and the diagnostics of the contained
process, never partial files as results.

A succeeded execution's numbers, table excerpts, distributions, figures and images can be
selected as evidence in a [presentation](presentations.md#evidence); the presentation
carries their unit, population, sample size and snapshot labels and keeps the
execution protected.

**Rerun on the same snapshot** recomputes the frozen inputs as a new execution.
**Refresh inputs** previews the current results of the same selection, says whether
the snapshot changed, and creates a distinct execution only after **Admit refreshed
analysis**; the original execution and its evidence stay unchanged. **Cancel
execution** is offered while it runs.

### Outputs

Outputs are ordered by meaning: numbers, summary tables (at most 50 rows), distributions,
images and figures, then whole datasets. When every output reads the same inputs, the
snapshot label (complete or partial with the counts), the valid inputs and the unavailable
inputs are said once above them; otherwise each output carries its own. Each output keeps
its inferential convention and the analysis' own qualifiers (population, aggregation,
window). Outputs are titled in words, with the emitted name beside the title.

- **Numbers** show the value with its unit. Floats read at four significant digits;
  **Full digits** shows every digit. Exact int64 values are always shown with all their
  digits, non-finite values as `NaN`, `+∞` and `−∞`, missing values as `—`.
- **Summary tables** are read whole, without paging or filters: a single row as labelled
  values, a few rows as a plain table, and a table of interval estimates (`mean`, `lower`,
  `upper`) as forest-plot rows grouped by metric, with **All columns** below. Each offers
  **Download CSV**.
- **Whole datasets** show every value exactly as saved. They are typed and paged on the server (50–500 rows per page). **Sort by**
  and **Add filter** (`eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, with `null`, `NaN`,
  `inf` and `-inf` accepted as values) build a derived index over the saved table on
  the server; the summary line says whether a page came from the saved order or from
  an index, and asks for a retry while an index is still being built. Nulls sort
  last, NaN after the infinities. **Download CSV** streams the whole table.
- **Distributions** show their counts and quantiles and name the retained
  representation: exact, binned or approximate.
- **Images** (PNG, SVG) are displayed as images loaded from the verified artifact;
  an SVG is never inlined into the page, so its scripts and links are inert. The
  artifact itself downloads as an attachment.
- **Plotly figures** render in a sandboxed frame without same-origin privileges,
  using the plotly.js version packaged with Studio. The page validates the saved
  specification first and refuses, with the reason, specifications that carry URLs
  (`layout.images[*].source`, `*src` references, `href`), templates by reference,
  script-like text, a `config` section or map and geo traces that need external
  tiles. A figure saved for another renderer version shows a **renderer mismatch**
  label instead of rendering. **Download PNG** exports a browser-rendered image, and
  the specification JSON downloads as saved. The frame never fetches anything.

The whole path works with tables alone: a project without plotting packages and
without figures loses nothing above. The analysis pages were verified with the
browser suite on Chromium, Firefox and WebKit (`studio/browser/analysis.spec.ts`:
network trap, bounded pages, exact int64 and non-finite labels, frame isolation,
the eight-of-24 journey and the missing-data refusal).

## Frozen snapshots and Python API

`AnalysisStore.preview(Selection(...))` records an immutable candidate with
planned/available/missing counts. It does not protect result files yet.
`admit(preview.id, definition_id=..., parameters=..., generation_id=...)` verifies
that exact evidence and creates its references plus waiting execution in one
transaction. If pruning won the race, admission asks for a fresh preview.
Publication after preview cannot silently add newer results to the candidate.

Use `prepare_analysis(store, registered_key)` from
`simulatte.analysis.command` to capture/inspect a definition. It returns the
immutable definition revision and environment generation IDs. Then use
`AnalysisRunner(store).submit(execution.id)` and `.wait(execution.id)`; close the
runner or use its context manager. `examples/studio_project/run_managed.py`
shows the complete simulation-to-analysis API without Studio.

Eight admitted successful replications remain eight after the experiment reaches
24. Pending, failed and unavailable statuses remain explicit in that snapshot.
`rerun` keeps the snapshot, source, environment and parameters; `refresh` explicitly
previews and admits current evidence under the same definition and parameters.
Definition edits create immutable revisions under the same registration UUID.
Annotations are separate append-only revisions and do not alter numerical output.

Definition and user policies combine by the stricter minimum and completeness
requirement, applied separately to valid inputs for every required dataset.
A successful run missing required fields is not a valid input for that dataset.
All original status/schema evidence stays in the snapshot; output metadata
includes per-dataset valid counts and missing-field reasons. Saved executions,
including cancelled ones, retain their evidence until explicitly deleted through
`AnalysisStore.delete`; active executions must first be cancelled. A result shared
by several saved analyses remains protected until its final owner is removed.

## Statistical interpretation and output contracts

`context.rows(name)` streams verified dataset rows; `context.scalars()` returns
replication-level scalar copies with run/configuration/index/seed/attempt identity.
`context.inputs` exposes copied read-only snapshot metadata: the selection,
experiments, configurations and counts, and for every replication its
run/configuration/index/seed/attempt/request identities, status and reason,
scalars, dataset manifests and artifact descriptors. Resolved manifests, requests
and fingerprints are not passed to the analysis; they remain in the store under
the snapshot hash recorded as `input_snapshot` in the output provenance. Saved
execution never queries current experiment results. Within-run rows are not
replications.

`summarize_population` is a pooled descriptive table with separate finite sample,
missing and non-finite counts. Empty samples have missing means, not zeros. It
uses bounded memory for at most 10,000 groups. `compare_replications` delegates
to the existing replication t-interval/paired-difference implementation. A single
replication is insufficient for an interval; missing pair indices and optional
Bonferroni family information remain visible. Distribution outputs declare whether
the retained representation is exact, binned or approximate.

`context.emit` accepts `Number`, `Table`, `Distribution`, `Figure` and `Image`.
Table iterators are spooled immediately. Names are unique portable identifiers.
Outputs retain units, population and statistical metadata; images/specifications
can be read without installing plotting packages. Limits are 1 GiB total output,
100,000 files and 4 MiB per metadata document. Larger numerical data belongs in a
streaming Table. Captured dataset manifests also use the 4 MiB bound; the input
document the runner writes for the analysis process is limited to 64 MiB.

Optional figures:

```python
from simulatte.analysis.adapters import save_figure
figure = collector.plot_wip(show=False)
context.emit("wip", save_figure(figure, temporary_directory, name="wip.svg"))
```

Install `simulatte[plot]`, `simulatte[analysis-seaborn]` or
`simulatte[analysis-plotly]` only when producing those figures. Existing plotting
helpers retain `show=True` by default and return their figure. Matplotlib and
Seaborn capture PNG/SVG. Plotly stores data-only figure JSON, never executable
HTML: `renderer.name="plotly.js"` and `renderer.version` identify the bundled
JavaScript renderer; `metadata.adapter_version` identifies the Python producer.
`figure.json` is stored in the tagged analytical JSON form (`$record`, `$int64`
and `$float64` wrappers); a consumer untags it with
`simulatte.analysis.codec.from_json` before handing it to plotly.js and treats
Plotly text fields as untrusted content. Studio packages plotly.js
`plotly.js-dist-min` at the version `plotly.offline.get_plotlyjs_version()`
reports for the pinned Python `plotly`, and renders only figures whose
`renderer.version` matches it (see [In Studio](#in-studio)).

Success requires verified artifacts, clean child/descendant cleanup, durable
rename and one index visibility transaction. Exceptions, disk failures,
cancellation or an unindexed directory after a crash never become successful
output. Input files are privately copied and read-only through the supported API.
Analysis remains trusted Python execution, not an operating-system sandbox.
