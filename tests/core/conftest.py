from __future__ import annotations

import pytest

from simulatte.utils.singleton import Singleton
from simulatte.shopfloor import ShopFloor


@pytest.fixture(autouse=True)
def reset_singletons():
    Singleton.clear()
    ShopFloor.servers = []
    yield
    Singleton.clear()
    ShopFloor.servers = []
