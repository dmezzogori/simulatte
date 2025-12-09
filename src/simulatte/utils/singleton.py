from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


class Singleton[T](type):
    """Metaclass implementing a simple singleton cache.

    Deprecated: retained only for backward compatibility with older tests.
    Prefer passing explicit instances instead of relying on global state.
    """

    _instances: dict[type[T], T] = {}

    def __call__(cls, *args, **kwargs):
        """
        The __call__ method implements the singleton pattern.

        It checks if an instance already exists for this class in
        the _instances dict.

        If not, it calls super().__call__(*args, **kwargs) to create
        a new instance, stores it in the _instances dict, and returns it.

        Otherwise, it returns the existing instance from the _instances dict.

        *args and **kwargs allow passing arguments when constructing the
        instance, like a normal __call__ method.

        This ensures only one instance will ever be created for this class.
        """

        if cls not in Singleton._instances:
            Singleton._instances[cls] = super().__call__(*args, **kwargs)
        return Singleton._instances[cls]

    @staticmethod
    def clear():
        """
        The clear method allows removing the instance for a given class
        from the _instances dict.

        It checks if the class is present in _instances.

        If so, it calls pop to remove it.

        This allows resetting the singleton instance for a given class,
        forcing a new one to be created on next access.
        """
        Singleton._instances.clear()
