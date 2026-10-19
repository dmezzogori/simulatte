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
Plotly text fields as untrusted content. Browser display requires a compatible
renderer, supplied by the viewer integration.

Success requires verified artifacts, clean child/descendant cleanup, durable
rename and one index visibility transaction. Exceptions, disk failures,
cancellation or an unindexed directory after a crash never become successful
output. Input files are privately copied and read-only through the supported API.
Analysis remains trusted Python execution, not an operating-system sandbox.
