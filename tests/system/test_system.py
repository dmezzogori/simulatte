from simulatte.environment import Environment
from simulatte.system import System


def test_system(env: Environment) -> None:
    system1 = System(env=env)
    system2 = System(env=env)
    assert system1 is system2
