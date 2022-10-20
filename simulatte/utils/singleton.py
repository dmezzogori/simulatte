class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

    def __getattr__(self, item):
        return getattr(self._instances[self], item)

    def clear(cls):
        if cls in cls._instances:
            cls._instances.pop(cls)
