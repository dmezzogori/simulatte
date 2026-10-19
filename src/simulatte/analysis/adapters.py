"""Lazy optional figure adapters; saved data and images need no plotting imports."""

import json
from pathlib import Path
from typing import Any, cast
from .outputs import Figure
from .types import identifier


def save_figure(figure: object, output_dir: Path, *, name: str) -> Figure:
    """Capture Matplotlib/Seaborn as PNG or SVG, Plotly as data-only JSON.

    A .svg/.png suffix selects the static format (PNG by default). Figure-level
    and axes-level Seaborn objects are accepted through their Matplotlib figure.
    """
    figure = cast(Any, figure)
    path = Path(name)
    suffix = path.suffix
    identifier(path.stem if suffix else name)
    if path.name != name or suffix not in ("", ".png", ".svg"):
        raise ValueError("Figure name must be a portable identifier with optional .png/.svg")
    module = type(figure).__module__.split(".")[0]
    if module == "plotly":
        import plotly

        payload = json.loads(figure.to_json())
        return Figure(
            "plotly",
            payload,
            {"adapter": "plotly", "representation": "figure_specification"},
            {"name": "plotly", "version": plotly.__version__},
        )
    if module in ("matplotlib", "seaborn"):
        import matplotlib
        from matplotlib.figure import Figure as MatplotlibFigure

        if not isinstance(figure, MatplotlibFigure):
            figure = figure.figure if hasattr(figure, "figure") else figure.get_figure()
        if not isinstance(figure, MatplotlibFigure):
            raise ValueError("Matplotlib/Seaborn adapter needs a figure or axes object")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        kind = suffix[1:] if suffix else "png"
        target = output_dir / (path.stem + "." + kind)
        if target.exists() or target.is_symlink():
            raise ValueError("Figure destination already exists")
        figure.savefig(target, format=kind)
        return Figure(
            kind,
            target,
            {"adapter": "matplotlib", "adapter_version": matplotlib.__version__, "representation": "static_image"},
        )
    raise ValueError("Unsupported live figure; install/use the Matplotlib, Seaborn or Plotly adapter")
