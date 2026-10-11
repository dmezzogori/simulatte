"""Worker ready must precede deferred commands, not merely env.run()."""

from __future__ import annotations

import pytest

from simulatte import Environment
from simulatte.environment import deferrable


class Component:
    def __init__(self, env, log):
        self.env, self.log = env, log

    @deferrable
    def submit(self):
        self.log.append("command")


def test_ready_barrier_order_and_frozen_manifest(tmp_path):
    assert hasattr(Environment, "_on_ready"), "ready barrier is missing"
    log = []
    params = {"x": 1.0}
    with Environment(seed=1, parameters=params, artifacts_dir=tmp_path) as env:
        params["x"] = 4.0
        Component(env, log).submit()
        env.on_activate(lambda: log.append("initializer"))
        env.request_projection(lambda state: log.append("initial"))

        def ready():
            assert env.activated
            assert env.manifest().requested["parameters"] == {"x": 1.0}
            assert env.artifacts_dir == tmp_path
            log.append("ready")

        env._on_ready(ready)
        env.activate()
        assert log == ["initializer", "initial", "ready", "command"]
        env.activate()
        assert len(log) == 4


def test_failed_ready_cannot_resume_queued_commands():
    assert hasattr(Environment, "_on_ready"), "ready barrier is missing"
    log = []
    with Environment(seed=1) as env:
        Component(env, log).submit()

        def fail():
            raise TimeoutError("cancelled")

        env._on_ready(fail)
        with pytest.raises(TimeoutError):
            env.activate()
        with pytest.raises(RuntimeError):
            env.activate()
        assert log == [] and env._run_failed


def test_build_guard_blocks_advance_but_allows_scheduling():
    assert hasattr(Environment, "_building"), "build guard is missing"
    with Environment(seed=1) as env:
        with env._building():
            env.timeout(2)
            for action in (env.run, env.step, env.activate):
                with pytest.raises(RuntimeError, match="build"):
                    action()
        env.run()
        assert env.now == 2
