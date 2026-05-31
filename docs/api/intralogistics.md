# Intralogistics API

All intralogistics symbols are exported from the single `simulatte.intralogistics` namespace.
For a conceptual overview and worked examples, see the
[Intralogistics guide](../guides/intralogistics.md) and the
[Intralogistics example gallery](../examples/intralogistics.md).

## Layout

::: simulatte.intralogistics.Node
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Arc
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.LayoutGraph
    options:
      heading_level: 3
      members: false

## Pathfinding

::: simulatte.intralogistics.PathPlanner
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.DijkstraPlanner
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.AStarPlanner
    options:
      heading_level: 3
      members: false

## Traffic

::: simulatte.intralogistics.TrafficManager
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.FreeTrafficManager
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ResourceBasedTrafficManager
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.PathCheckResult
    options:
      heading_level: 3
      members: false

## Products & storage

::: simulatte.intralogistics.SKU
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Warehouse
    options:
      heading_level: 3
      members: false

## Vehicles

::: simulatte.intralogistics.AGV
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.AGVType
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.AGVState
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.SpeedProfile
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.TrapezoidalProfile
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Battery
    options:
      heading_level: 3
      members: false

## Facilities

::: simulatte.intralogistics.ChargingStation
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ParkingArea
    options:
      heading_level: 3
      members: false

## Orders

::: simulatte.intralogistics.TransferOrder
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.OrderStatus
    options:
      heading_level: 3
      members: false

## Orchestration

::: simulatte.intralogistics.FleetCoordinator
    options:
      heading_level: 3
      members: false

## Policies

::: simulatte.intralogistics.DispatchStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.NearestIdleStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.RoundRobinStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ReplenishmentPolicy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ReorderPointPolicy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.RepositioningPolicy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.RepositioningContext
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.StayInPlace
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.NearestParkingPolicy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.LoadRecoveryStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ReturnToOrigin
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ResumeDelivery
    options:
      heading_level: 3
      members: false

## Metrics

::: simulatte.intralogistics.OrderMetricsCollector
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.EMAOrderMetrics
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.IntralogisticsTimeSeriesCollector
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.DefaultIntralogisticsCollector
    options:
      heading_level: 3
      members: false

## Builders

::: simulatte.intralogistics.build_simple_system
    options:
      heading_level: 3
      members: false
