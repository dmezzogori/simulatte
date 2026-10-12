"""Run an experiment headlessly from a script (design 4.1), on the local coordinator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from simulatte.execution.coordinator import Coordinator
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.experiments.definition import Experiment, load_experiment


def project_root(file: str | Path | None, project: str | Path | None) -> Path:
    """``project`` when given, else the nearest directory holding ``pyproject.toml`` above ``file``."""
    if project is not None:
        return Path(project).resolve()
    start = Path.cwd() if file is None else Path(file).resolve().parent
    return next((path for path in (start, *start.parents) if (path / "pyproject.toml").is_file()), start)


def run(
    experiment: Experiment | str | Path,
    project: str | Path | None = None,
    workers=None,
    timeout: float | None = None,
    *,
    slots: SlotConfig | None = None,
) -> dict[str, Any]:
    """Prepare, expand and execute ``experiment`` to completion; returns ``Coordinator.wait_experiment``.

    ``experiment`` is a definition or the path of an ``*.experiment.json`` file; the
    project root defaults to the file's (or the working directory's) nearest
    ``pyproject.toml``. ``timeout`` bounds the preparation and each replication.
    """
    file = None if isinstance(experiment, Experiment) else Path(experiment)
    definition = experiment if isinstance(experiment, Experiment) else load_experiment(experiment)
    root = project_root(file, project)
    spec = discover_project(root / definition.model.partition(":")[0], root)
    with Coordinator(spec, reference=definition.model, slots=slots) as coordinator:
        run_id = coordinator.start_experiment(definition, workers=workers, timeout=timeout)
        return coordinator.wait_experiment(run_id)
