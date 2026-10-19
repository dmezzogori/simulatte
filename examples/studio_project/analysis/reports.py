"""Named analyses over retained evidence; no plotting dependency."""

from simulatte.analysis import DatasetRequirement, analysis
from simulatte.model import ParameterSchema


@analysis(parameters=ParameterSchema(()), requires=(DatasetRequirement("job_outcomes", ("family", "lateness")),))
def lateness(context, parameters):
    from simulatte.analysis.reports import summarize_population

    rows = (row for row in context.rows("job_outcomes") if row["included"])
    context.emit("lateness_by_family", summarize_population(rows, value="lateness", group_by=("family",)))


@analysis(
    parameters=ParameterSchema(()), requires=(DatasetRequirement("agv_durations", ("agv_id", "state", "duration")),)
)
def utilization(context, parameters):
    from simulatte.analysis.builtins import fleet_utilization
    from simulatte.analysis.outputs import Number

    values = fleet_utilization(context.rows("agv_durations"))
    for key in ("mean_of_vehicle_fractions", "ratio_of_durations"):
        context.emit(key, Number(values[key], unit="fraction", metadata={"convention": "descriptive_within_run"}))
