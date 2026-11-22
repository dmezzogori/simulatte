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

## Routing

A router finds the routes of a fleet's travels and its cost ranks the candidates of the coordinator's decisions. A cost is in the router's own unit and is comparable only within one router. See the [routing section of the guide](../guides/intralogistics.md#routing).

::: simulatte.intralogistics.Router
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.RouteRequest
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Route
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ShortestDistanceRouter
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.FastestRouter
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.CongestionAwareRouter
    options:
      heading_level: 3
      members: false

## Traffic

A traffic controller grants each AGV the resources it needs to move. `FreeFlowController` is the default; `ReservationController` enforces the occupancy of nodes, segments, zones and corridors. See the [traffic section of the guide](../guides/intralogistics.md#traffic-control).

::: simulatte.intralogistics.TrafficController
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.intralogistics.FreeFlowController
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ReservationController
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Zone
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.TrafficView
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

### Policies and reactions

::: simulatte.intralogistics.TrafficPolicy
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.intralogistics.StandardTrafficPolicy
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.KeepWaiting
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Reroute
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.StepAside
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.GiveUp
    options:
      heading_level: 3
      members: false

### Protocol values

The values a controller and a policy exchange with the travel executor. Custom controllers and policies use them; models that only configure the built-in ones do not need them.

::: simulatte.intralogistics.Claim
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ClaimItem
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.ClaimRequest
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Segment
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Corridor
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.WaitHooks
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Wait
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Blocker
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Disposition
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Granted
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.Rerouted
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.SteppedAside
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.GaveUp
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
      members: [battery_level_now, accrue_idle_power]

::: simulatte.intralogistics.AGVType
    options:
      heading_level: 3
      members: [depletion_fn, load_time, unload_time, length, min_gap, corner_speed, turn_time, idle_power]

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

::: simulatte.intralogistics.Kinematics
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
      members: [make_way, best_bay, service_nodes, agv_report]

## Policies

::: simulatte.intralogistics.DispatchContext
    options:
      heading_level: 3
      members: [cost]

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
      members: [cost]

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

Built-in fleet collectors on the event bus; `TrafficKPIs` reports nothing for a fleet under `FreeFlowController`. Each is bound to its fleet coordinator and attached with
`collector.attach(env)`; every `FleetCoordinator` attaches an `OrderEMACollector` as `coordinator.metrics` unless
built with `default_metrics=False`.

::: simulatte.intralogistics.OrderEMACollector
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.FleetTimeSeries
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.intralogistics.FleetKPIs
    options:
      heading_level: 3
      members: false

::: simulatte.intralogistics.TrafficKPIs
    options:
      heading_level: 3
      members: false

## Builders

::: simulatte.intralogistics.build_simple_system
    options:
      heading_level: 3
      members: false

## Cancellation and recovery

Cancelling a terminal order is a no-op, including a completed order whose AGV is still repositioning.
When `ReturnToOrigin` successfully returns cargo after an interruption, the order goes back into the pending queue.
Interrupts received during recovery are merged until rollback finishes; cancellation remains terminal.
Idle callbacks run after the previous mission's ownership and order retirement have been updated.

Interrupted charging and battery swaps release granted slots or cancel waiting requests. A swap interrupted before
installation returns its battery to the pool. An interrupt received while an AGV waits for a claim takes effect at
once, where it waits; one received while it moves takes effect at the first node where it can come to rest. Entering
a parking area again for the same parked AGV keeps its existing slot.
