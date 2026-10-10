# Core API

The core of Simulatte is a small set of cooperating objects: `Environment` drives the SimPy
clock and carries the event bus and the log sinks; `ShopFloor` is the central orchestrator that tracks WIP, routes
jobs, and fires hooks; `ProductionJob` represents a unit of work that moves through a sequence
of `Server` resources via the `Router`; `PreShopPool` holds jobs before they are released to
the floor; and `Runner` repeats multiple simulation replications with independent random seeds.
See the [architecture diagram](../introduction/architecture.md) for how these objects interact.

## Core objects

::: simulatte.environment.Environment
    options:
      heading_level: 3
      members: true

::: simulatte.shopfloor.ShopFloor
    options:
      heading_level: 3
      members: true

::: simulatte.server.Server
    options:
      heading_level: 3
      members: true

::: simulatte.job.ProductionJob
    options:
      heading_level: 3
      members: true

::: simulatte.router.Router
    options:
      heading_level: 3
      members: true

::: simulatte.psp.PreShopPool
    options:
      heading_level: 3
      members: true

::: simulatte.runner.Runner
    options:
      heading_level: 3
      members: true

## Extension points

::: simulatte.shopfloor.OperationHook
    options:
      heading_level: 3
      members: false

::: simulatte.shopfloor.WIPStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.shopfloor.StandardWIPStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.shopfloor.CorrectedWIPStrategy
    options:
      heading_level: 3
      members: false

::: simulatte.shopfloor.Dispatcher
    options:
      heading_level: 3
      members: false

## Collectors

Built-in collectors on the event bus (`simulatte.collectors`). Each is bound to its owner (a shop floor or a server) and
attached with `collector.attach(env)`; every `ShopFloor` attaches an `EMACollector` as `shopfloor.metrics` unless built
with `default_metrics=False`.

::: simulatte.collectors.EMACollector
    options:
      heading_level: 3
      members: false

::: simulatte.collectors.ShopFloorTimeSeries
    options:
      heading_level: 3
      members: true

::: simulatte.collectors.CurrentWorkloadCollector
    options:
      heading_level: 3
      members: false

::: simulatte.collectors.ServerTimeSeries
    options:
      heading_level: 3
      members: true

::: simulatte.collectors.ShopFloorKPIs
    options:
      heading_level: 3
      members: false
