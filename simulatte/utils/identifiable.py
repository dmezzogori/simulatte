from __future__ import annotations

from itertools import count


class Identifiable(type):
    """
    Metaclass that assigns a unique id to each instance of a class.
    """

    id: int
    classes: set = set()

    def __call__(cls, *args, **kwargs):
        """
        Assigns a unique id to each instance of a class.
        """

        # Add class to set of classes that use Identifiable metaclass
        Identifiable.classes.add(cls)

        # Create id iterator and instances dictionary if they don't exist on the class
        if not hasattr(cls, "_id_iter"):
            cls._id_iter = count()
            cls._instances = {}

        # Create instance and assign id
        _instance = super().__call__(*args, **kwargs)
        _instance.id = next(cls._id_iter)

        # Add instance to instances dictionary
        cls._instances[_instance.id] = _instance

        return _instance

    @staticmethod
    def reset():
        """
        Resets the id iterator and instances dictionary for each class that uses Identifiable metaclass.
        """

        for cls in Identifiable.classes:
            cls._id_iter = count()
            cls._instances = {}
