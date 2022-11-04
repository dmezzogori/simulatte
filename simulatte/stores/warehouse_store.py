from simulatte.location import Location
from simulatte.stores.warehouse_location.warehouse_location import WarehouseLocation


class WarehouseStore:
    locin: Location
    locout: Location
    name: str

    locations: tuple[WarehouseLocation]

    def load_ant(self, *args, **kwargs):
        raise NotImplementedError

    def get(self, *args, **kwargs):
        raise NotImplementedError
