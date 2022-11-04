import pytest

from simulatte.environment import Environment
from simulatte.system import System


@pytest.fixture(scope="function")
def env() -> Environment:
    Environment.clear()
    yield Environment()
    Environment.clear()


@pytest.fixture(scope="function")
def system(env: Environment) -> System:
    return System(env=env)
