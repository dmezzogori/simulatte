import pytest

from simulatte.environment import Environment


@pytest.fixture(scope="function")
def env():
    return Environment()
