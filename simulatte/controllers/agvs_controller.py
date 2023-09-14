from __future__ import annotations

from collections.abc import Collection, Generator
from typing import TYPE_CHECKING

from ..agv.agv_kind import AGVKind

if TYPE_CHECKING:
    from simulatte.agv import AGV, AGVMission
    from simulatte.controllers import SystemController
    from simulatte.policies import AGVSelectionPolicy


class AGVController:
    """
    Represent a controller for a group of agvs.
    The controller holds a reference to all the agvs and is responsible for managing their missions.
    The controller is responsible for selecting the best agv for a given mission, according to the set selection policy.
    """

    __slots__ = (
        "agvs",
        "_agv_selection_policy",
        "_feeding_agvs",
        "_replenishment_agvs",
        "_input_agvs",
        "_output_agvs",
        "system_controller",
    )

    def __init__(self, *, agvs: Collection[AGV], agv_selection_policy: AGVSelectionPolicy):
        self.system_controller = None
        self.agvs = agvs
        self._agv_selection_policy = agv_selection_policy
        self._feeding_agvs: tuple[AGV, ...] | None = None
        self._replenishment_agvs: tuple[AGV, ...] | None = None
        self._input_agvs: tuple[AGV, ...] | None = None
        self._output_agvs: tuple[AGV, ...] | None = None

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

    def register_system(self, system: SystemController):
        """
        Register the system controller.
        """

        self.system_controller = system

    def agvs_missions(self, *, agvs: Collection[AGV] | None = None) -> Generator[AGVMission, None, None]:
        """
        Return a generator of all the missions of the given agvs.
        If no agvs are given, return a generator of all the missions of all the agvs.
        """

        if agvs is None:
            agvs = self.agvs
        return (mission for agv in agvs for mission in agv.missions)

    def best_agv(self, *, agvs: Collection[AGV] | None = None, exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Return the best AGV according to the set selection policy.
        """

        if agvs is None:
            agvs = self.agvs

        return self._agv_selection_policy(agvs=agvs, exceptions=exceptions)

    def best_feeding_agv(self, exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Return the best feeding AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.feeding_agvs, exceptions=exceptions)

    def best_replenishment_agv(self, exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Return the best replenishment AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.replenishment_agvs, exceptions=exceptions)

    def best_input_agv(self, exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Return the best input AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.input_agvs, exceptions=exceptions)

    def best_output_agv(self, exceptions: Collection[AGV] | None = None) -> AGV:
        """
        Return the best output AGV according to the set selection policy.
        """

        return self.best_agv(agvs=self.output_agvs, exceptions=exceptions)
