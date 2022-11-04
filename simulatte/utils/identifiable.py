from itertools import count


class Identifiable(type):

    id: int

    def __call__(cls, *args, **kwargs):
        if not hasattr(cls, "_id_iter"):
            cls._id_iter = count()
            cls._instances = {}
        _instance = super(Identifiable, cls).__call__(*args, **kwargs)
        _instance.id = next(cls._id_iter)
        cls._instances[_instance.id] = _instance
        return _instance

    def __getitem__(cls, item):
        return cls._instances[item]
