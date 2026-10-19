"""Descriptive rows, replication-level inference and lazy figure capture."""

import math
import sys

import pytest

from simulatte.analysis import codec
from simulatte.analysis.adapters import save_figure
from simulatte.analysis.context import AnalysisContext
from simulatte.analysis.outputs import Number, Table, Distribution, Figure, Image
from simulatte.analysis.publication import verify
from simulatte.analysis.reports import summarize_population, compare_replications
from simulatte.analysis.types import DatasetSchema, Field


def context(tmp_path):
    return AnalysisContext(
        {"replications": [], "available": 0, "planned": 1, "partial": True},
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "outputs",
    )


def test_population_descriptive_missingness_and_groups():
    table = summarize_population(
        [
            {"family": "A", "lateness": 1},
            {"family": "A", "lateness": 3},
            {"family": "A", "lateness": None},
            {"family": "B", "lateness": math.inf},
            {"family": "B", "lateness": math.nan},
        ],
        value="lateness",
        group_by=("family",),
        unit="hours",
        population="included",
    )
    rows = list(table.rows)
    assert rows[0] == {"family": "A", "n": 2, "missing": 1, "nonfinite": 0, "mean": 2.0, "min": 1.0, "max": 3.0}
    assert rows[1]["n"] == 0 and rows[1]["nonfinite"] == 2 and rows[1]["mean"] is None
    assert table.metadata["inferential_convention"] == "pooled_descriptive"
    assert table.metadata["population"] == "included"
    assert next(f for f in table.schema.fields if f.name == "mean").unit == "hours"
    assert list(summarize_population([], value="lateness").rows)[0]["mean"] is None
    with pytest.raises(ValueError, match="retained"):
        summarize_population([{"value": 1}], value="value", group_by=("absent",))


def test_replication_comparison_uses_indices_and_reports_missing():
    snapshot = {
        "configurations": [
            {"run": "experiment", "index": i, "label": label} for i, label in enumerate(("base", "other"))
        ],
        "selection": {"experiment_run_ids": ["experiment"], "run_ids": []},
        "experiments": [{"name": "test"}],
        "partial": True,
        "replications": [],
    }
    for c, i, status, scalar in (
        (0, 0, "succeeded", 10),
        (0, 1, "succeeded", 12),
        (1, 0, "succeeded", 15),
        (1, 1, "failed", None),
    ):
        snapshot["replications"].append(
            {
                "run": "experiment",
                "configuration": c,
                "index": i,
                "status": status,
                "available": status == "succeeded",
                "scalars": {} if scalar is None else {"lateness": scalar},
                "attempt": f"{c}-{i}",
            }
        )
    table = compare_replications(snapshot, metric="lateness", baseline="base", bonferroni=True)
    row = list(table.rows)[0]
    assert row["n"] == 1 and row["mean"] == 5 and row["lower"] is None and row["state"] == "insufficient"
    assert row["missing_indices"] == [1]
    assert table.metadata["inferential_convention"] == "replication_paired_t"
    assert table.metadata["pointwise"] is False and table.metadata["partial_snapshot"] is True
    assert list(compare_replications(snapshot, metric="lateness").rows)[0]["n"] == 2


def test_outputs_exact_numeric_table_distribution_and_validation(tmp_path):
    ctx = context(tmp_path)
    ctx.emit("integer", Number(2**60, unit="items"))
    ctx.emit("missing", Number(None))
    schema = DatasetSchema("samples", (Field("value", "float64", nullable=True),))
    ctx.emit("samples", Table(schema, iter([{"value": math.nan}, {"value": None}])))
    ctx.emit("distribution", Distribution({"representation": "exact", "n": 2}, "samples"))
    with pytest.raises(ValueError, match="unique"):
        ctx.emit("integer", Number(2))
    manifest = ctx.finish("execution", {})
    assert verify(tmp_path / "outputs", "execution") == manifest.to_dict()
    raw = codec.loads((tmp_path / "outputs/integer/number.json").read_bytes())
    assert raw["value"] == 2**60 and raw["metadata"]["partial_snapshot"] is True
    with pytest.raises(ValueError):
        context(tmp_path / "other").emit("../escape", Number(1))
    with pytest.raises(ValueError, match="representation"):
        context(tmp_path / "distribution").emit("bad", Distribution({}))


@pytest.mark.parametrize("suffix", ["png", "svg"])
def test_matplotlib_capture_and_legacy_show(tmp_path, monkeypatch, suffix):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from simulatte.collectors import ShopFloorTimeSeries

    collector = ShopFloorTimeSeries.__new__(ShopFloorTimeSeries)
    collector.wip_ts = [(0, 1), (1, 2)]
    shown = []
    monkeypatch.setattr(plt, "show", lambda: shown.append(True))
    figure = collector.plot_wip(show=False)
    assert not shown and figure is not None
    collector.plot_wip()
    assert shown == [True]
    captured = save_figure(figure, tmp_path / "figures", name="wip." + suffix)
    ctx = context(tmp_path)
    ctx.emit("figure", captured)
    ctx.finish("execution", {})
    output = verify(tmp_path / "outputs", "execution")["outputs"][0]
    assert output["metadata"]["media_type"] == ("image/png" if suffix == "png" else "image/svg+xml")
    plt.close("all")


def test_plotly_data_only_roundtrip(tmp_path):
    import plotly.graph_objects as go

    fig = go.Figure(data=go.Bar(x=["A"], y=[3]))
    saved = save_figure(fig, tmp_path, name="plot")
    assert saved.kind == "plotly" and saved.renderer["name"] == "plotly.js"
    ctx = context(tmp_path)
    ctx.emit("plot", saved)
    ctx.finish("execution", {})
    output = verify(tmp_path / "outputs", "execution")["outputs"][0]
    assert output["renderer"]["version"]
    assert codec.loads((tmp_path / "outputs/plot/figure.json").read_bytes())["data"][0]["y"] == [3]


def test_saved_outputs_without_plot_dependencies(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    ctx.emit("number", Number(1))
    image = tmp_path / "tiny.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    ctx.emit("image", Image(image, "image/png"))
    ctx.emit("spec", Figure("plotly", {"data": [], "layout": {}}, renderer={"name": "plotly.js", "version": "test"}))
    ctx.finish("execution", {})
    for name in ("matplotlib", "seaborn", "plotly", "pandas", "numpy"):
        monkeypatch.setitem(sys.modules, name, None)
    assert len(verify(tmp_path / "outputs", "execution")["outputs"]) == 3
    with pytest.raises(ValueError, match="adapter"):
        save_figure(object(), tmp_path, name="unknown")


def test_seaborn_capture(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    axes = sns.lineplot(x=[0, 1], y=[1, 3])
    captured = save_figure(axes, tmp_path, name="seaborn")
    assert captured.kind == "png" and captured.payload.is_file()
    plt.close("all")


def test_output_paths_and_budgets_are_enforced(tmp_path, monkeypatch):
    from simulatte.analysis import context as module

    ctx = context(tmp_path)
    with pytest.raises(ValueError, match="adapter"):
        ctx.emit("unsupported", object())
    ctx.emit("Number", Number(1))
    with pytest.raises(ValueError, match="collide"):
        ctx.emit("number", Number(1))
    with pytest.raises(ValueError, match="Number requires"):
        context(tmp_path / "bad").emit("bool", Number(True))
    with pytest.raises(ValueError, match="renderer"):
        context(tmp_path / "plotly").emit("bad", Figure("plotly", {}))
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")
    link = tmp_path / "link.png"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Invalid image"):
        context(tmp_path / "links").emit("bad", Image(link, "image/png"))
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 4)
    with pytest.raises(ValueError, match="budget"):
        context(tmp_path / "large").emit(
            "table", Table(DatasetSchema("values", (Field("value", "string"),)), [{"value": "too large"}])
        )
    monkeypatch.setattr(module, "MAX_MANIFEST_BYTES", 1)
    with pytest.raises(ValueError, match="4 MiB"):
        context(tmp_path / "document").emit("value", Number(1))


def test_plotly_renderer_identity_is_javascript_not_python_producer(tmp_path):
    import plotly
    import plotly.graph_objects as go
    from plotly.offline import get_plotlyjs_version

    saved = save_figure(go.Figure(), tmp_path, name="renderer")
    assert saved.renderer == {"name": "plotly.js", "version": get_plotlyjs_version()}
    assert saved.metadata["adapter"] == "plotly"
    assert saved.metadata["adapter_version"] == plotly.__version__


def test_summary_groups_keep_missing_labels_and_reject_invalid_inputs():
    table = summarize_population(
        [{"family": None, "x": 1}, {"family": "A", "x": 2}, {"family": None, "x": 3}], value="x", group_by=("family",)
    )
    assert [(row["family"], row["n"], row["mean"]) for row in table.rows] == [(None, 2, 2.0), ("A", 1, 2.0)]
    dtype = {f.name: f.dtype for f in table.schema.fields}["family"]
    assert dtype == "string"
    only_missing = summarize_population([{"family": None, "x": 1}], value="x", group_by=("family",))
    assert {f.name: f.dtype for f in only_missing.schema.fields}["family"] == "null"
    for rows, message in (
        ([{"family": ["A"], "x": 1}], "scalar"),
        ([{"family": "A", "x": 1}, {"family": 2, "x": 1}], "inconsistent"),
        ([{"family": "A", "x": "1"}], "numerical or missing"),
        ([{"family": "A", "x": True}], "numerical or missing"),
    ):
        with pytest.raises(ValueError, match=message):
            summarize_population(rows, value="x", group_by=("family",))


def test_summary_group_count_is_bounded():
    rows = ({"group": i, "x": 1} for i in range(10_001))
    with pytest.raises(ValueError, match="10000 groups"):
        summarize_population(rows, value="x", group_by=("group",))


def test_figure_adapter_refuses_invalid_names_objects_and_overwrites(tmp_path):
    from matplotlib.figure import Figure as MatplotlibFigure

    for name in ("../escape", "dir/fig", "fig.pdf", ".png"):
        with pytest.raises(ValueError):
            save_figure(MatplotlibFigure(), tmp_path, name=name)
    detached = type("Detached", (), {"__module__": "matplotlib.fake", "figure": None})()
    with pytest.raises(ValueError, match="figure or axes"):
        save_figure(detached, tmp_path, name="detached")
    first = save_figure(MatplotlibFigure(), tmp_path / "figures", name="plot.svg")
    assert first.kind == "svg" and first.payload.is_file()
    with pytest.raises(ValueError, match="already exists"):
        save_figure(MatplotlibFigure(), tmp_path / "figures", name="plot.svg")
    first.payload.unlink()
    first.payload.symlink_to(tmp_path / "missing.svg")
    with pytest.raises(ValueError, match="already exists"):
        save_figure(MatplotlibFigure(), tmp_path / "figures", name="plot.svg")


def test_analytical_json_rejects_unexact_numbers_and_unknown_tags():
    for value in (
        {"$int64": "5"},  # representable exactly as a JSON number: tag must not be used
        {"$int64": "007"},
        {"$int64": str(2**63)},
        {"$int64": 5},
        {"$float64": "1.5"},
        {"$unknown": 1},
        {"$record": 1},
        2**53,
        math.inf,
    ):
        with pytest.raises(ValueError):
            codec.from_json(value)
    assert codec.from_json({"$int64": str(2**53)}) == 2**53
    assert codec.from_json(2**53 - 1) == 2**53 - 1
