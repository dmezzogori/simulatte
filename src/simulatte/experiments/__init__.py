"""Experiments: configurations x replications of one prepared model (D92, D96).

All APIs are unstable. Definitions are ``experiment-v1`` documents with the
published schema ``schema/experiment-v1.schema.json``.
"""

from simulatte.experiments.definition import (
    Experiment,
    Explicit,
    Grid,
    LayoutVariant,
    Replications,
    experiment_id,
    experiment_type,
    load_experiment,
)
from simulatte.experiments.expansion import Configuration, ExperimentRun, expand
from simulatte.experiments.runner import run

__all__ = [
    "Configuration",
    "Experiment",
    "ExperimentRun",
    "Explicit",
    "Grid",
    "LayoutVariant",
    "Replications",
    "expand",
    "experiment_id",
    "experiment_type",
    "load_experiment",
    "run",
]
