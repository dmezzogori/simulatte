"""Research wrapper; the two previous fixtures remain byte-identical copies."""

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path

from simulatte import model

if __package__:
    from . import coupled_case, fixed_case, projection
else:
    import coupled_case
    import fixed_case
    import projection


@dataclass(frozen=True)
class Params:
    fixture: str = "fixed"
    engine: str = "bounded"
    policy: str = "spt"
    hard_limit: int = 12
    wip_cap: int = 4
    burst: int = 3
    interval: float = 0.5
    feedback: bool = True
    record: bool = True


@model(params=Params, horizon=96)
def build(env, layout, params):
    assert params.fixture in ("fixed", "coupled")
    case = fixed_case if params.fixture == "fixed" else coupled_case
    values = asdict(params)
    del values["fixture"]
    original = case.Controller
    if os.environ.get("SIMULATTE_IDENTITIES_DISABLE") == "1":
        return case.build(env, layout, case.Params(**values))
    every = int(os.environ.get("SIMULATTE_IDENTITIES_QUERY_EVERY", "0"))
    assert every >= 0
    controllers = []

    class Instrumented(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.actions = projection.Actions()
            self.reconstructed = hashlib.sha256()
            self.query_count = self.max_raw = self.max_view = 0
            self.in_poll = False
            self.cached_observations = self.cached_kpis = None
            controllers.append(self)

        def observations(self):
            value = super().observations()
            if self.in_poll:
                self.cached_observations = value
            return value

        def kpis(self):
            value = super().kpis()
            if self.in_poll:
                self.cached_kpis = value
            return value

        def poll(self):
            start, tick = self.cursor, self.tick
            self.in_poll = True
            super().poll()
            self.in_poll = False
            jobs = []
            if params.fixture == "fixed":
                target = self.params.hard_limit // 2 if self.tight_mode else self.params.hard_limit
                for index in range(start, self.cursor):
                    spec = case.specification(self.seed, index)
                    record = dict(spec, arrival=self.live[index][1])
                    self.reconstructed.update(case.encoded(record))
                    jobs.append({k: record[k] for k in ("id", "arrival", "route", "durations", "due")})
                assert self.reconstructed.hexdigest() == self.admissions_hash.hexdigest()
            else:
                tight = self.params.feedback and (self.cached_kpis["tardy_fraction"] or 0) > 0.35
                target = self.params.hard_limit // 2 if tight else self.params.hard_limit
                pool = any(o["state"] == "pool" for o in self.cached_observations)
                choice = "short" if self.params.feedback and pool else "normal"
                for index in range(start, self.cursor):
                    record = case.job_wire(case.candidate(self.seed, index, self.env.now, choice))
                    self.reconstructed.update(case.encoded(record))
                    jobs.append(dict(id=index, **record))
                assert self.reconstructed.hexdigest() == self.jobs_hash.hexdigest()
            self.actions.update(self.env.now, tick, target, jobs)
            self.cached_observations = self.cached_kpis = None
            if every and self.tick % every == 0:
                self.view()

        def view(self):
            if params.engine == "python":
                return None
            value, size = projection.capture(self.adapter, self.state, self.actions.state())
            projection.validate(value)
            self.query_count += 1
            self.max_raw = max(self.max_raw, size["raw_bytes"])
            self.max_view = max(self.max_view, size["view_bytes"])
            return value

    # This is a single-process model build; restore the fixture class immediately.
    case.Controller = Instrumented
    try:
        case.build(env, layout, case.Params(**values))
    finally:
        case.Controller = original
    controller = controllers[0]
    initial = []
    env.on_activate(lambda: initial.append(controller.view()))

    def finish():
        value = dict(
            schema=projection.SET_SCHEMA,
            initial=initial[0],
            final=controller.view(),
            actions=controller.actions.state(),
        )
        data = case.encoded(value)
        (env.artifacts_dir / "identities.json").write_bytes(data)
        directory = os.environ.get("SIMULATTE_IDENTITY_DIAGNOSTICS")
        if directory:
            Path(directory, str(os.getpid()) + ".json").write_bytes(
                case.encoded(
                    dict(
                        queries=controller.query_count,
                        max_raw_bytes=controller.max_raw,
                        max_view_bytes=controller.max_view,
                        artifact_bytes=len(data),
                    )
                )
            )

    # After the original finalizer, before managed backend.finish(), at the same boundary.
    case.wake(env, env._execution_policy.until, priority=-1, callback=finish)
