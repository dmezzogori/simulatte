# Numerical measurements

Measurements retain declared numerical evidence independently of animation.
Ordinary Python and managed runs use the same collectors. Studio is optional.
The runnable `examples/studio_project` contains production and fleet models,
their measurement declarations, named analyses and input data.

## Job lateness in ordinary Python

Attach collectors before generating jobs when arrival-time attributes matter:

```python
from simulatte import Environment
from simulatte.analysis import Population
from simulatte.analysis.builtins import JobOutcomeCollector

with Environment(seed=17) as env:
    outcomes = JobOutcomeCollector(
        fields={"family": ("string", lambda job: job.family)},
        populations=(Population("family_A", lambda row: row["family"] == "A",
                                observed_at="arrival", fields=("family",)),),
        membership_at="arrival",
    )
    outcomes.attach(env)
    # Build your shop floor, admit jobs, then run the environment.
    env.run(until=30)

rows = outcomes.rows
```

The example models supply complete runnable setup. From the example folder,
`python run_numerical.py` runs both models and retains rows in memory without a
trace. `python run_managed.py` publishes KPI-level runs and executes both named
analyses using captured code and inputs. Run these with the Simulatte version
containing the analysis API installed in your interpreter.

`JobOutcomeCollector` observes declared attributes at `arrival` or `completion`.
Its arrival boundary is first PSP/shop-floor admission; the retained arrival time
is the job's `created_at`. Completion membership can use lateness and other
completion evidence. Arrival membership cannot use future outcomes. Overlapping
populations are allowed. Predicate arguments are copied, recursively read-only
observations, never live jobs or environments. Extractors and predicates are
trusted Python callbacks with an observational contract; this is not a Python
sandbox. Callback failure records measurement failure without changing baseline
simulation results.

Building jobs before attaching a collector is supported before activation:
completion attributes are observed when those jobs finish. Arrival attributes
which were not observed are null, membership is unavailable and `missing_reason`
explains why. Completed-before-attachment history is excluded and counted in
`limitations`; it is not reconstructed from current attributes. Unfinished jobs
produce explicit censored rows. The `included` field applies the declared cohort
and warm-up policy. Filter it when reporting completed observations.

## Fleet utilization and time-dependent populations

`AgvDurationCollector(fields=..., populations=...)` retains intervals at
`membership_at="interval_start"`. State changes close the old interval and open
the next, preserving copied attributes. If a classification changes without a
state event, call `collector.observe_boundary(agv)` at that declared boundary.
Intervals are clipped to the observation window; zero-duration observations do
not become synthetic utilization samples.

The example's utilization analysis emits two separately named results:
`mean_of_vehicle_fractions` gives equal weight to each observed vehicle;
`ratio_of_durations` gives weight proportional to observed duration. They can
differ when vehicles have unequal observation durations. Capabilities may be
retained as typed lists and used in combined predicates. A posthoc predicate
requiring an unretained field reports insufficient evidence. Aggregate-only
history cannot answer a temporal predicate.

## In Studio

Declared measurements are retained when a run's request selects a measurement plan.
`run_managed.py` does this through `measurements=` of `Coordinator.make_request`;
an experiment file does it with its `measurements` entry, so experiment replications
started from **Experiments** retain their datasets. An interactive **Run** from a
model page does not select a plan yet: it keeps KPIs (and a recording when the
**Recording** level is a full recording), and an analysis that requires a dataset
from such a run receives the missing-data screen rather than a result.

**KPIs only** on the model page keeps the numerical outputs without a trace; the
run page and the Runs page say so, and such a run remains a valid analysis input.
Analyses read datasets from the snapshot of the selected runs; the [Analyses
pages](analysis.md#in-studio) show the per-dataset valid counts and the reasons for
unavailable inputs.

## Declarations, storage and exact values

A model declares one or more `MeasurementPlan` objects through
`@model(..., measurement_plans=(plan,))`. An execution selects a declaration with
`select_measurements(plan)` from `simulatte.execution.schema` and passes it as
`measurements=` to `Coordinator.make_request`. The selection changes request
identity. Omitted measurements preserve legacy request hashes. Dispatch requires
the worker capability `analytical-artifacts-v1`.

For ordinary Python persistence, call
`env.configure_measurements(plan, output_dir=path)` before attaching collectors.
Without an output directory rows are retained in memory by explicit choice.
Persisted collectors stream typed rows; they do not accumulate the whole dataset.
Dataset manifests preserve schema, units, attribute timing, window, cohort,
clipping, censoring, limitations and ordered checksummed chunks.

The `analytical-dataset-v1` bounds are:

| Item | Limit |
| --- | --- |
| Chunk | 4,096 rows and 1 MiB encoded bytes |
| Single row | 256 KiB encoded bytes |
| JSON page | 1–500 rows and 1 MiB encoded response |
| CSV encoded record | 8 MiB characters; decoded row still 256 KiB |

`DatasetReader.iter_rows()` streams all rows. `.page(limit=100)` returns a bounded
page with an identity-bound continuation cursor. `.to_csv(destination)` streams
the entire dataset, regardless of any UI page. `read_csv(source, schema)` is the
module-level function in `simulatte.analysis.datasets`.

CSV is a documented single-line-record dialect: every cell is tagged JSON,
including strings; JSON escapes embedded newlines. The companion DatasetSchema
is required for import. Structured cells and large Unicode cells round-trip
without changing Python's process-global CSV field limit. Multiline external
CSV requires conversion to this dialect. Oversized data raises an error; no
export truncates silently.

Stored int64, null, NaN and signed infinities remain distinct. JSON uses
`$int64` decimal strings outside JavaScript's safe integer range, `$float64`
non-finite tags and `$record` wrappers to avoid collisions with user records.
Use `simulatte.analysis.codec.to_json/from_json` at a JSON boundary. A dataframe
can be constructed from the iterator with a package of your choice; no dataframe
library is needed to import, run or read the numerical API.
