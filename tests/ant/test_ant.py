import pytest

from simulatte.environment import Environment
from simulatte.ant import Ant
from simulatte.unitload import CaseContainer


@pytest.fixture(scope="function")
def ant(env: Environment) -> Ant:
    return Ant(env)


def test_load(env: Environment, ant: Ant):
    def test():
        assert ant.unit_load is None

        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)
        assert ant.unit_load is unit_load

    env.process(test())
    env.run()


def test_double_load(env: Environment, ant: Ant):
    def test():
        unit_load = CaseContainer()
        yield ant.load(unit_load=unit_load)

        with pytest.raises(ValueError):
            yield ant.load(unit_load=unit_load)

    env.process(test())
    env.run()


def test_release_free(env: Environment, ant: Ant):
    def test():
        with pytest.raises(ValueError):
            yield ant.release_current()

    env.process(test())
    env.run()
