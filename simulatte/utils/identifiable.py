from itertools import count


class Identifiable(type):

    id: int
    classes: set = set()

    def __call__(cls, *args, **kwargs):
        Identifiable.classes.add(cls)
        if not hasattr(cls, "_id_iter"):
            cls._id_iter = count()
            cls._instances = {}
        _instance = super(Identifiable, cls).__call__(*args, **kwargs)
        _instance.id = next(cls._id_iter)
        cls._instances[_instance.id] = _instance
        return _instance

    @staticmethod
    def reset():
        for cls in Identifiable.classes:
            cls._id_iter = count()
            cls._instances = {}
