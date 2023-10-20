from __future__ import annotations


class Singleton(type):
    """
    Singleton is a metaclass that implements the singleton design pattern.

    It ensures only one instance of a class exists and manages access to that
    instance.

    The _instances dict stores the instances for each subclass of Singleton.

    The __call__ method checks if an instance already exists for the called
    class. If not, it creates one by calling super and stores it in _instances.

    The __getattr__ method proxies attribute access to the stored instance.

    The clear method allows removing the instance for a given class from
    _instances.

    Any class that subclasses Singleton will have at most one instance,
    accessible via the class or any instances. Further calls to the class
    will return the same instance.
    """

    _instances = {}
    classes: set = set()

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

        if cls not in cls._instances:
            Singleton.classes.add(cls)
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

    def __getattr__(self, item):
        return getattr(self._instances[self], item)

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

        for cls in Singleton.classes:
            cls._instances = {}
