"""Bounded scalar observation schedules for opt-in managed backends."""

from dataclasses import dataclass

from simulatte.stopping import Horizon, _finite


@dataclass(frozen=True, slots=True, kw_only=True)
class BackendObservation:
    """Opt-in scalar snapshots; at most max_samples points per declared KPI.

    Interval is in simulation time. A finer request is coarsened to cover the
    entire reporting window, including warmup and the right-open horizon.
    """

    interval: float = 1.0
    max_samples: int = 1000

    def __post_init__(self):
        interval = _finite(self.interval, "observation interval")
        if interval <= 0:
            raise ValueError("observation interval must be positive")
        if type(self.max_samples) is not int or not 2 <= self.max_samples <= 4096:
            raise ValueError("observation max_samples must be an integer between 2 and 4096")
        object.__setattr__(self, "interval", interval)

    def times(self, policy: Horizon) -> tuple[float, ...]:
        """Cover warmup through horizon, coarsening rather than truncating the tail."""
        stride = max(self.interval, (policy.until - policy.warmup) / (self.max_samples - 1))
        result = [policy.warmup]
        for i in range(1, self.max_samples - 1):
            at = policy.warmup + i * stride
            if at >= policy.until:
                break
            if at > result[-1]:
                result.append(at)
        result.append(policy.until)
        return tuple(result)

    def to_dict(self, policy: Horizon):
        return dict(
            schema=1,
            kind="scalar_snapshots",
            boundary="right_open",
            requested_interval=self.interval,
            effective_interval=max(self.interval, (policy.until - policy.warmup) / (self.max_samples - 1)),
            max_samples=self.max_samples,
            scheduled_samples=len(self.times(policy)),
            start=policy.warmup,
            end=policy.until,
        )
