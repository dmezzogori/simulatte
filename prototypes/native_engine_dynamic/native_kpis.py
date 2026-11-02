"""Scalar-only native path and an adapter over unmodified production KPIs.

Research scope: one fixed shop floor, unit-capacity servers, FIFO/SPT, capped
release. No trace, Python publisher, observation journal or history collectors.
"""

from simulatte.collectors import ShopFloorKPIs

from .compiled import CompiledStream
from .native import _number
from .production import ProductionStream


def _warmup(value):
    value = _number(value)
    if value < 0:
        raise ValueError("warmup must be nonnegative")
    return value


class NativeKPIStream(CompiledStream):
    def __init__(self, servers, *, wip_cap=12, policy="spt", warmup=0):
        warmup = _warmup(warmup)
        super().__init__(servers, wip_cap=wip_cap, policy=policy, mode="none")
        try:
            self._module.enable_stock_kpis(self._engine, warmup)
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        with self._lock:
            self._check()
            return self._module.stock_kpis(self._engine)


class ProductionKPIStream(ProductionStream):
    def __init__(self, servers, *, wip_cap=12, policy="spt", warmup=0):
        self._warmup = _warmup(warmup)
        super().__init__(servers, wip_cap=wip_cap, policy=policy, mode="none")

    def configure_observers(self):
        self.env.configure_kpis(warmup=self._warmup)
        self.stock = ShopFloorKPIs(self.shop).attach(self.env)

    def snapshot(self):
        if self.closed:
            raise RuntimeError("stream is closed")
        window = self.stock.window
        return dict(window=(window.start, window.end, window.closed), scalars=self.stock.scalars())
