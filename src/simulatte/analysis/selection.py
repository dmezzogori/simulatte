"""Explicit, data-only input selection. No implicit latest-run query."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Selection:
    run_ids: tuple[str, ...] = ()
    experiment_run_ids: tuple[str, ...] = ()
    configurations: tuple[int, ...] | None = None
    require_complete: bool = False
    min_replications: int = 0

    def __post_init__(self):
        for values in (self.run_ids, self.experiment_run_ids):
            if (
                type(values) is not tuple
                or any(type(v) is not str or not v for v in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("Selection needs unique explicit IDs")
        if not self.run_ids and not self.experiment_run_ids:
            raise ValueError("Select explicit runs or experiments")
        if self.configurations is not None and (
            type(self.configurations) is not tuple
            or not self.configurations
            or any(type(v) is not int or v < 0 for v in self.configurations)
            or len(set(self.configurations)) != len(self.configurations)
        ):
            raise ValueError("Invalid configuration indices")
        if (
            type(self.require_complete) is not bool
            or type(self.min_replications) is not int
            or self.min_replications < 0
        ):
            raise ValueError("Invalid completeness policy")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            tuple(data["run_ids"]),
            tuple(data["experiment_run_ids"]),
            None if data["configurations"] is None else tuple(data["configurations"]),
            data["require_complete"],
            data["min_replications"],
        )
