from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from simulatte.agv.agv_kind import AGVKind
from simulatte.environment import Environment
from simulatte.reporting import render_table
from simulatte.utils import EnvMixin

if TYPE_CHECKING:
    from simulatte.agv.agv import AGV


def least_busy_agv(*, agvs: Iterable[Any], exceptions: Iterable[Any] | None = None) -> Any:
    """
    Pick the AGV with the fewest users/queued missions.
    """

    pool = [agv for agv in agvs if exceptions is None or agv not in exceptions]
    if not pool:
        raise ValueError("No AGVs available for selection")
    return min(pool, key=lambda agv: (agv.n_users, agv.n_queue))


def idle_feeding_order(*, agvs: Iterable[Any], exceptions: Iterable[Any] | None = None) -> list[Any]:
    """
    Return feeding AGVs ordered from least to most loaded.
    """

    pool = [agv for agv in agvs if exceptions is None or agv not in exceptions]

    def sorter(agv: AGV):
        timestamp = 0
        mission = getattr(agv, "current_mission", None)
        if mission is not None:
            timestamp = mission.start_time or mission.request.time
        return agv.n_users, agv.n_queue, timestamp

    return sorted(pool, key=sorter)


class AGVController(EnvMixin):
    """
    Controller for a group of AGVs.
    Selection is now just a pair of callables instead of bespoke policy classes.
    """

    def __init__(
        self,
        *,
        agvs,
        select_agv: Callable[..., Any] = least_busy_agv,
        order_idle: Callable[..., list[Any]] = idle_feeding_order,
        env: Environment,
    ) -> None:
        EnvMixin.__init__(self, env=env)

        self.agvs = agvs
        self._select_agv = select_agv
        self._order_idle = order_idle
        self._feeding_agvs: tuple[Any, ...] | None = None
        self._replenishment_agvs: tuple[Any, ...] | None = None
        self._input_agvs: tuple[Any, ...] | None = None
        self._output_agvs: tuple[Any, ...] | None = None

    @property
    def feeding_agvs(self):
        """
        Return a tuple of all the feeding AGVs.
        Feeding AGVs are those used to feed the picking cells.
        """

        # Lazy initialization
        if self._feeding_agvs is None:
            self._feeding_agvs = tuple(agv for agv in self.agvs if agv.kind == AGVKind.FEEDING)

        return self._feeding_agvs

    @property
    def replenishment_agvs(self):
        """
        Return a tuple of all the replenishment AGVs.
        Replenishment AGVs are those used for replenishment operations.
        """

        # Lazy initialization
        if self._replenishment_agvs is None:
            self._replenishment_agvs = tuple(agv for agv in self.agvs if agv.kind == AGVKind.REPLENISHMENT)

        return self._replenishment_agvs

    @property
    def input_agvs(self):
        """
        Return a tuple of all the input AGVs.
        Input AGVs are those used for input operations to the system.
        """

        # Lazy initialization
        if self._input_agvs is None:
            self._input_agvs = tuple(agv for agv in self.agvs if agv.kind == AGVKind.INPUT)

        return self._input_agvs

    @property
    def output_agvs(self):
        """
        Return a tuple of all the output AGVs.
        Output AGVs are those used for retrieving the output of picking cells.
        """

        # Lazy initialization
        if self._output_agvs is None:
            self._output_agvs = tuple(agv for agv in self.agvs if agv.kind == AGVKind.OUTPUT)

        return self._output_agvs

    def agvs_missions(self, *, agvs=None):
        """
        Return a generator of all the missions of the given agvs.
        If no agvs are given, return a generator of all the missions of all the agvs.
        """

        if agvs is None:
            agvs = self.agvs
        return (mission for agv in agvs for mission in agv.missions)

    def best_agv(self, *, agvs=None, exceptions=None):
        """
        Return the best AGV according to the selection strategy.
        """

        if agvs is None:
            agvs = self.agvs

        return self._select_agv(agvs=agvs, exceptions=exceptions)

    def best_feeding_agv(self, exceptions=None):
        """
        Return the best feeding AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.feeding_agvs, exceptions=exceptions)

    def best_replenishment_agv(self, exceptions=None):
        """
        Return the best replenishment AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.replenishment_agvs, exceptions=exceptions)

    def best_input_agv(self, exceptions=None):
        """
        Return the best input AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.input_agvs, exceptions=exceptions)

    def best_output_agv(self, exceptions=None):
        """
        Return the best output AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.output_agvs, exceptions=exceptions)

    def ordered_idle_feeding_agvs(self, exceptions=None) -> list[Any]:
        """
        Ordered list of feeding AGVs, useful when we need more than one candidate.
        """

        return self._order_idle(agvs=self.feeding_agvs, exceptions=exceptions)

    def summary(self, *, render: bool = True):
        headers = ["KPI", "Valore", "U.M."]
        table = [
            ["Ore simulate", f"{self.env.now / 3600:.2f}", "[h]"],
            ["Numero di AGV", len(self.agvs), "[n]"],
            ["Numero di FeedingAGV", len(self.feeding_agvs), "[n]"],
            ["Numero di ReplenishmentAGV", len(self.replenishment_agvs), "[n]"],
            ["Numero di InputAGV", len(self.input_agvs), "[n]"],
            ["Numero di OutputAGV", len(self.output_agvs), "[n]"],
            ["Numero di missioni", len(tuple(self.agvs_missions())), "[n]"],
            ["Numero di missioni FeedingAGV", len(tuple(self.agvs_missions(agvs=self.feeding_agvs))), "[n]"],
            [
                "Numero di missioni ReplenishmentAGV",
                len(tuple(self.agvs_missions(agvs=self.replenishment_agvs))),
                "[n]",
            ],
            ["Numero di missioni InputAGV", len(tuple(self.agvs_missions(agvs=self.input_agvs))), "[n]"],
            ["Numero di missioni OutputAGV", len(tuple(self.agvs_missions(agvs=self.output_agvs))), "[n]"],
        ]

        feeding_agvs_stats = {}
        for agv in self.feeding_agvs:
            d = feeding_agvs_stats.setdefault(
                agv.picking_cell, {"idle_times": [], "waiting_times": [], "travel_times": []}
            )
            d["idle_times"].append(agv.idle_time / 3600)
            d["waiting_times"].append(agv.waiting_time / 3600)
            d["travel_times"].append(agv._travel_time / 3600)

        for picking_cell, stats in feeding_agvs_stats.items():
            table.extend(
                [
                    [
                        f"Tempo medio di idle - FeedingAGV {picking_cell.__name__}",
                        statistics.mean(stats["idle_times"]),
                        "[h]",
                    ],
                    [
                        f"Tempo medio di attesa - FeedingAGV {picking_cell.__name__}",
                        statistics.mean(stats["waiting_times"]),
                        "[h]",
                    ],
                    [
                        f"Tempo medio di viaggio - FeedingAGV {picking_cell.__name__}",
                        statistics.mean(stats["travel_times"]),
                        "[h]",
                    ],
                ]
            )

        if render:
            render_table("Performance Summary of AGVs fleet", headers, table)

        return {"headers": headers, "rows": table}
