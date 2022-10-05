import pytest
from simpy import Environment


@pytest.fixture(scope="function")
def env():
    return Environment()
