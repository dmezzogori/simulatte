"""Warehouse-to-warehouse material transport by AGV fleets.

The names of ``__all__`` are imported from their modules on first use, so that importing one module of the package,
such as ``graph`` for the network of a layout, does not import the fleet, traffic and AGV modules (spec §9 of
``specs/2026-11-23-layout-only-models-design.md``).
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulatte.intralogistics.agv import AGV, AGVState, AGVType
    from simulatte.intralogistics.battery import Battery
    from simulatte.intralogistics.builders import build_simple_system
    from simulatte.intralogistics.charging import ChargingStation
    from simulatte.intralogistics.fleet import FleetCoordinator
    from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
    from simulatte.intralogistics.kinematics import Kinematics
    from simulatte.intralogistics.metrics import FleetKPIs, FleetTimeSeries, OrderEMACollector, TrafficKPIs
    from simulatte.intralogistics.network import Claim, ClaimItem, Corridor, Segment, Zone
    from simulatte.intralogistics.order import OrderStatus, TransferOrder
    from simulatte.intralogistics.parking import ParkingArea
    from simulatte.intralogistics.policies import (
        DispatchContext,
        DispatchStrategy,
        LoadRecoveryStrategy,
        NearestIdleStrategy,
        NearestParkingPolicy,
        ReorderPointPolicy,
        ReplenishmentPolicy,
        RepositioningContext,
        RepositioningPolicy,
        ResumeDelivery,
        ReturnToOrigin,
        RoundRobinStrategy,
        StayInPlace,
    )
    from simulatte.intralogistics.reservation import ReservationController
    from simulatte.intralogistics.routing import (
        CongestionAwareRouter,
        FastestRouter,
        Route,
        Router,
        RouteRequest,
        ShortestDistanceRouter,
    )
    from simulatte.intralogistics.sku import SKU
    from simulatte.intralogistics.speed import SpeedProfile, TrapezoidalProfile
    from simulatte.intralogistics.traffic import (
        Blocker,
        ClaimRequest,
        Disposition,
        FreeFlowController,
        GaveUp,
        GiveUp,
        Granted,
        KeepWaiting,
        Reroute,
        Rerouted,
        StepAside,
        SteppedAside,
        TrafficController,
        TrafficPolicy,
        TrafficView,
        Wait,
        WaitHooks,
    )
    from simulatte.intralogistics.traffic_policy import StandardTrafficPolicy
    from simulatte.intralogistics.warehouse import Warehouse

_MODULES = {
    "AGV": "agv",
    "AGVState": "agv",
    "AGVType": "agv",
    "Battery": "battery",
    "build_simple_system": "builders",
    "ChargingStation": "charging",
    "FleetCoordinator": "fleet",
    "Arc": "graph",
    "LayoutGraph": "graph",
    "Node": "graph",
    "Kinematics": "kinematics",
    "FleetKPIs": "metrics",
    "FleetTimeSeries": "metrics",
    "OrderEMACollector": "metrics",
    "TrafficKPIs": "metrics",
    "Claim": "network",
    "ClaimItem": "network",
    "Corridor": "network",
    "Segment": "network",
    "Zone": "network",
    "OrderStatus": "order",
    "TransferOrder": "order",
    "ParkingArea": "parking",
    "DispatchContext": "policies",
    "DispatchStrategy": "policies",
    "LoadRecoveryStrategy": "policies",
    "NearestIdleStrategy": "policies",
    "NearestParkingPolicy": "policies",
    "ReorderPointPolicy": "policies",
    "ReplenishmentPolicy": "policies",
    "RepositioningContext": "policies",
    "RepositioningPolicy": "policies",
    "ResumeDelivery": "policies",
    "ReturnToOrigin": "policies",
    "RoundRobinStrategy": "policies",
    "StayInPlace": "policies",
    "ReservationController": "reservation",
    "CongestionAwareRouter": "routing",
    "FastestRouter": "routing",
    "Route": "routing",
    "Router": "routing",
    "RouteRequest": "routing",
    "ShortestDistanceRouter": "routing",
    "SKU": "sku",
    "SpeedProfile": "speed",
    "TrapezoidalProfile": "speed",
    "Blocker": "traffic",
    "ClaimRequest": "traffic",
    "Disposition": "traffic",
    "FreeFlowController": "traffic",
    "GaveUp": "traffic",
    "GiveUp": "traffic",
    "Granted": "traffic",
    "KeepWaiting": "traffic",
    "Reroute": "traffic",
    "Rerouted": "traffic",
    "StepAside": "traffic",
    "SteppedAside": "traffic",
    "TrafficController": "traffic",
    "TrafficPolicy": "traffic",
    "TrafficView": "traffic",
    "Wait": "traffic",
    "WaitHooks": "traffic",
    "StandardTrafficPolicy": "traffic_policy",
    "Warehouse": "warehouse",
}
"""The module of each exported name."""

__all__ = [
    # graph
    "Node",
    "Arc",
    "LayoutGraph",
    # routing
    "Router",
    "Route",
    "RouteRequest",
    "ShortestDistanceRouter",
    "FastestRouter",
    "CongestionAwareRouter",
    # traffic
    "TrafficController",
    "TrafficView",
    "TrafficPolicy",
    "FreeFlowController",
    "ReservationController",
    "StandardTrafficPolicy",
    "KeepWaiting",
    "Reroute",
    "StepAside",
    "GiveUp",
    "Zone",
    # controller protocol values
    "Claim",
    "ClaimItem",
    "ClaimRequest",
    "Segment",
    "Corridor",
    "WaitHooks",
    "Wait",
    "Blocker",
    "Disposition",
    "Granted",
    "Rerouted",
    "SteppedAside",
    "GaveUp",
    # sku
    "SKU",
    # agv
    "AGV",
    "AGVType",
    "AGVState",
    # speed
    "SpeedProfile",
    "TrapezoidalProfile",
    "Kinematics",
    # battery
    "Battery",
    # warehouse
    "Warehouse",
    # charging
    "ChargingStation",
    # parking
    "ParkingArea",
    # order
    "OrderStatus",
    "TransferOrder",
    # policies
    "DispatchContext",
    "DispatchStrategy",
    "NearestIdleStrategy",
    "RoundRobinStrategy",
    "ReplenishmentPolicy",
    "ReorderPointPolicy",
    "RepositioningPolicy",
    "RepositioningContext",
    "StayInPlace",
    "NearestParkingPolicy",
    "LoadRecoveryStrategy",
    "ReturnToOrigin",
    "ResumeDelivery",
    # fleet
    "FleetCoordinator",
    # metrics
    "OrderEMACollector",
    "FleetTimeSeries",
    "FleetKPIs",
    "TrafficKPIs",
    # builders
    "build_simple_system",
]


def __getattr__(name: str) -> Any:
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
