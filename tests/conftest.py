from __future__ import annotations

import pytest
import simulatte


@pytest.fixture(scope="function")
def env() -> simulatte.Environment:
    simulatte.Environment.clear()
    yield simulatte.Environment()
    simulatte.Environment.clear()


@pytest.fixture(scope="function")
def system(env: simulatte.Environment) -> simulatte.SystemController:
    return simulatte.SystemController(env=env)
