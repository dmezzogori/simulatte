from simulatte.system import System
from simulatte.utils import Singleton


def test_system() -> None:
    system1 = System()
    system2 = System()
    assert system1 is system2

    class MySystem(System, metaclass=Singleton):
        pass

    print(dir(MySystem))

    assert MySystem() is MySystem()
