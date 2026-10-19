"""Expand a definition against a pinned preparation into ordinary execution requests (D96, D103).

Expansion is pure: it reads the prepared model's metadata and the caller's loaded
layout variants and runtimes, and never touches a worker or the store.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import itertools
from typing import Any

from simulatte.execution.coordinator import PreparedModel, selected_model
from simulatte.execution.schema import ExecutionRequest, _hash, _mapping, _plain, _runtime, content_id
from simulatte.execution.store import experiment_run_id
from simulatte.experiments.definition import Experiment, Explicit, LayoutVariant, experiment_id
from simulatte.layout.identity import layout_hash
from simulatte.model import ParameterSchema, validate_parameters

RUNTIME_KINDS = ("local", "uv")


@dataclass(frozen=True, slots=True)
class Configuration:
    """One parameter set on one layout variant."""

    index: int
    label: str
    parameters: Mapping[str, Any]
    layout: str
    layout_hash: str
    layout_ref: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """The mapping ``RunStore.create_experiment_run`` records for this configuration."""
        return {
            "label": self.label,
            "parameters": _plain(self.parameters),
            "layout_hash": self.layout_hash,
            "layout_ref": _plain(self.layout_ref),
        }


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    """Requests keyed ``(configuration, replication, runtime kind)``; ``replications`` in store shape."""

    experiment_id: str
    run_id: str
    configurations: tuple[Configuration, ...]
    requests: Mapping[tuple[int, int, str], ExecutionRequest]
    replications: tuple[dict[str, Any], ...]


def _value(value: Any) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    return repr(value) if type(value) is float else str(value)


def _label(names, parameters) -> str:
    return ",".join(f"{name}={_value(parameters[name])}" for name in names) or "defaults"


def _parameter_sets(experiment: Experiment, schema: ParameterSchema):
    configurations = experiment.configurations
    if isinstance(configurations, Explicit):
        for label, items in configurations.configurations:
            given = dict(items)
            values = validate_parameters(schema, given)
            yield (label or _label(given, values)), values
        return
    names = [name for name, _ in configurations.parameters]
    for combination in itertools.product(*(values for _, values in configurations.parameters)):
        values = validate_parameters(schema, dict(zip(names, combination)))
        yield _label(names, values), values


def _variants(experiment, prepared, layouts):
    project = prepared.snapshot.source.project
    variants = experiment.layouts or (LayoutVariant("base", project.layout),)
    unknown = set(layouts) - {variant.label for variant in variants}
    if unknown:
        raise ValueError(f"Unknown layout variants: {sorted(unknown)}")
    for variant in variants:
        if variant.file != project.layout and variant.file not in project.layouts:
            raise ValueError(
                f"Layout variant {variant.file!r} must be the configured layout or listed in "
                "[tool.simulatte.bundle] layouts"
            )
        if variant.label in layouts:
            identity, reference = layouts[variant.label]
            _hash(identity)
            yield variant.label, identity, _mapping(reference)
        elif variant.file == project.layout:
            yield variant.label, layout_hash("file", prepared.layout), prepared.documents["layout_ref"]
        else:
            raise ValueError(f"Layout variant {variant.label!r} is not loaded")


def expand(
    experiment: Experiment,
    prepared: PreparedModel,
    layouts: Mapping[str, tuple[str, Mapping[str, Any]]],
    runtime_by_kind: Mapping[str, Mapping[str, Any]],
) -> ExperimentRun:
    """One request per configuration, replication and runtime kind, at the prepared revision.

    ``layouts`` maps variant labels to ``(layout_hash, document_ref)``; a variant on the
    configured layout file may be omitted and then uses the prepared layout.
    ``runtime_by_kind`` maps ``local`` to the prepared runtime and ``uv`` to a provisioning spec.
    """
    if type(experiment) is not Experiment:
        raise ValueError("expand needs an Experiment")
    if not runtime_by_kind or not set(runtime_by_kind) <= set(RUNTIME_KINDS):
        raise ValueError("Runtime kinds must be local and/or uv")
    runtimes = {}
    for kind in sorted(runtime_by_kind):
        runtime = _runtime(runtime_by_kind[kind])
        worker_kind = "local" if runtime.get("kind") == "local-installation-v1" else runtime.get("kind", "local")
        if worker_kind != kind:
            raise ValueError(f"The {kind} runtime mapping has a different kind")
        runtimes[kind] = runtime
    path, separator, _ = experiment.model.partition(":")
    if experiment.model != prepared.model and (separator or path != prepared.model.partition(":")[0]):
        raise ValueError("Experiment model differs from the prepared model")
    selected = selected_model(prepared.metadata)
    schema = ParameterSchema.from_dict(selected["parameters"])
    stopping = selected["stopping"] if experiment.stopping is None else experiment.stopping.to_dict()
    variants = list(_variants(experiment, prepared, layouts))
    configurations: list[Configuration] = []
    seen, labels = set(), set()
    for parameters_label, parameters in _parameter_sets(experiment, schema):
        frozen = _mapping(parameters)
        for layout_label, identity, reference in variants:
            label = f"{parameters_label}/{layout_label}"
            if (frozen, identity) in seen or label in labels:
                raise ValueError(f"Duplicate configuration {label!r}")
            seen.add((frozen, identity))
            labels.add(label)
            configurations.append(Configuration(len(configurations), label, frozen, layout_label, identity, reference))
    replications = experiment.replications
    requests: dict[tuple[int, int, str], ExecutionRequest] = {}
    identities: dict[tuple[int, int], list[str]] = {}
    for configuration in configurations:
        for index in range(replications.count):
            ids = identities[(configuration.index, index)] = []
            for kind, runtime in runtimes.items():
                request = ExecutionRequest(
                    model=prepared.model,
                    source_hash=prepared.snapshot.source.content_hash,
                    input_hash=prepared.snapshot.inputs.content_hash,
                    layout_hash=configuration.layout_hash,
                    layout_revision=prepared.revision.number,
                    parameters=configuration.parameters,
                    seed=replications.base_seed + index,
                    stopping=stopping,
                    level=experiment.level,
                    kpis=experiment.kpis,
                    measurements=experiment.measurements,
                    runtime=runtime,
                )
                requests[(configuration.index, index, kind)] = request
                ids.append(content_id(request))
    # Replication-major order: early replications of every configuration first (D96).
    rows: tuple[dict[str, Any], ...] = tuple(
        {
            "configuration": configuration.index,
            "index": index,
            "seed": replications.base_seed + index,
            "content_ids": identities[(configuration.index, index)],
        }
        for index in range(replications.count)
        for configuration in configurations
    )
    definition = experiment_id(experiment)
    run_id = experiment_run_id(definition, [identity for ids in identities.values() for identity in ids])
    return ExperimentRun(definition, run_id, tuple(configurations), requests, rows)
