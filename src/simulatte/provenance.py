"""Provenance and the run manifest (spec §9.3).

A :class:`RunManifest` describes a run in two parts. The *requested* part is fixed by activation: software
versions, platform, dependencies, RNG derivation, seed, parameters, time unit and warm-up. The *final* part
is known at the end of the run: the stopping policy, the owners of opaque samplers and whether the run is
*complete*, that is whether it can be reproduced from the manifest (no :data:`UNAVAILABLE` field and no
opaque sampler). Volatile metadata (wall-clock start, host, durations) lives in :class:`VolatileMetadata`
and is never part of a comparison.
"""

from __future__ import annotations

import functools
import platform
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Final

from simulatte._wire import FrozenMap, Wire, freeze
from simulatte.rng import RNG_DERIVATION

__all__ = [
    "UNAVAILABLE",
    "Provenance",
    "RunManifest",
    "VolatileMetadata",
    "build_final",
    "build_requested",
]

UNAVAILABLE: Final = "unavailable"
"""Placeholder of a provenance hash that was not supplied."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Hashes that identify what a run was made of; each is a hash string or :data:`UNAVAILABLE`."""

    model: str = UNAVAILABLE
    source: str = UNAVAILABLE
    inputs: str = UNAVAILABLE
    dependencies: str = UNAVAILABLE


@dataclass(frozen=True, slots=True)
class RunManifest:
    """The manifest of a run: the `requested` part and, once a run finished, the `final` part."""

    requested: FrozenMap
    final: FrozenMap | None

    @property
    def complete(self) -> bool:
        """Whether the run can be reproduced from its manifest; False until a run produced the final part."""
        return self.final is not None and self.final["complete"] is True

    def merged(self) -> FrozenMap:
        """The requested and final fields in one map (the keys of the two parts are disjoint)."""
        if self.final is None:
            return self.requested
        return FrozenMap({**self.requested, **self.final})


@dataclass(frozen=True, slots=True)
class VolatileMetadata:
    """Facts about one execution that differ between identical runs; never compare them."""

    host: str
    wall_clock_start: str | None
    run_seconds: float


def volatile_metadata(wall_clock_start: str | None, run_seconds: float) -> VolatileMetadata:
    return VolatileMetadata(host=socket.gethostname(), wall_clock_start=wall_clock_start, run_seconds=run_seconds)


def build_requested(
    *,
    seed: int,
    time_unit: str | None,
    provenance: Provenance | None,
    parameters: Mapping[str, object] | None = None,
    warmup: float = 0.0,
) -> FrozenMap:
    """The requested part of a manifest."""
    given = provenance if provenance is not None else Provenance()
    if given.dependencies != UNAVAILABLE:
        dependencies: Wire = freeze({"source": "provenance", "hash": given.dependencies})
    else:
        dependencies = freeze({"source": "installed-distributions", "packages": _installed_distributions()})
    return FrozenMap(
        {
            "simulatte_version": _simulatte_version(),
            "python": freeze(
                {"implementation": platform.python_implementation(), "version": platform.python_version()}
            ),
            "platform": freeze({"system": platform.system(), "machine": platform.machine()}),
            "dependencies": dependencies,
            "provenance": freeze(
                {
                    "model": given.model,
                    "source": given.source,
                    "inputs": given.inputs,
                    "dependencies": given.dependencies,
                }
            ),
            "rng_derivation": RNG_DERIVATION,
            "seed": str(seed),
            "parameters": freeze(parameters or {}),
            "time_unit": time_unit,
            "warmup": float(warmup),
        }
    )


def build_final(requested: FrozenMap, stopping_policy: Wire, opaque_sampler_owners: Iterable[str]) -> FrozenMap:
    """The final part of a manifest, given its requested part."""
    owners = tuple(opaque_sampler_owners)
    return FrozenMap(
        {
            "stopping_policy": stopping_policy,
            "opaque_sampler_owners": owners,
            "complete": not owners and not _contains_unavailable(requested),
        }
    )


def _contains_unavailable(value: Wire) -> bool:
    if value == UNAVAILABLE:
        return True
    if isinstance(value, FrozenMap):
        return any(_contains_unavailable(v) for v in value.values())
    return False


def _simulatte_version() -> str:
    try:
        return metadata.version("simulatte")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from an uninstalled source tree
        return UNAVAILABLE


@functools.cache
def _installed_distributions() -> dict[str, str]:
    """Name-to-version listing of the installed distributions, scanned once per process."""
    packages: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:  # pragma: no branch - a distribution without a name is a broken installation
            packages.setdefault(name.lower().replace("_", "-"), dist.version)
    return dict(sorted(packages.items()))
