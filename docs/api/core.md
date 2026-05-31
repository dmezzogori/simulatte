# Core API

The core of Simulatte is a small set of cooperating objects: `Environment` drives the SimPy
clock and carries the logger; `ShopFloor` is the central orchestrator that tracks WIP, routes
jobs, and fires hooks; `ProductionJob` represents a unit of work that moves through a sequence
of `Server` resources via the `Router`; `PreShopPool` holds jobs before they are released to
the floor; and `Runner` repeats multiple simulation replications with independent random seeds.
See the [architecture diagram](../introduction/architecture.md) for how these objects interact.

## Environment

::: simulatte.environment.Environment
    options:
      heading_level: 3
      members: true

## ShopFloor

::: simulatte.shopfloor.ShopFloor
    options:
      heading_level: 3
      members: true

## Server

::: simulatte.server.Server
    options:
      heading_level: 3
      members: true

## ProductionJob

::: simulatte.job.ProductionJob
    options:
      heading_level: 3
      members: true

## Router

::: simulatte.router.Router
    options:
      heading_level: 3
      members: true

## PreShopPool

::: simulatte.psp.PreShopPool
    options:
      heading_level: 3
      members: true

## Runner

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

::: simulatte.shopfloor.MetricsCollector
    options:
      heading_level: 3
      members: false

::: simulatte.shopfloor.Dispatcher
    options:
      heading_level: 3
      members: false
