from simulatte.utils import Singleton


def test_system() -> None:
    class MyClass(metaclass=Singleton):
        pass

    assert MyClass() is MyClass()
