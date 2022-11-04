import pytest

from simulatte.environment import Environment
from simulatte.system import System


@pytest.fixture(scope="function")
def env() -> Environment:
    return Environment()


@pytest.fixture(scope="function")
def system(env: Environment) -> System:
    return System(env=env)
