from __future__ import annotations

from itertools import count

_id_iter: dict[type, count] = {}


class IdentifiableMixin:
    """
    Mixin that assigns a unique id to each instance of a class.
    """

    id: int

    def __init__(self):
        if _id_iter.get(self.__class__) is None:
            _id_iter[self.__class__] = count()
        self.id = next(_id_iter[self.__class__])

    def __str__(self):
        return f"{self.__class__.__name__}[{self.id}]"

    @staticmethod
    def clear():
        """
        Resets the id iterator and instances dictionary for each class that uses Identifiable metaclass.
        """
        global _id_iter
        for cls in _id_iter:
            _id_iter[cls] = count()
