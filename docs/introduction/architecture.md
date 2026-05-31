# Core Concepts & Architecture

Simulatte models two complementary domains — production planning (job-shop) and intralogistics (warehouse and AGV) — both built on a shared SimPy `Environment` that drives the simulation clock and event queue. The two domains can run independently or side by side within the same simulation; their orchestrators (`ShopFloor` and `FleetCoordinator`) coordinate all domain-specific behaviour while delegating timing to the common environment.

## Production planning

A `ProductionJob` carries a server sequence, processing times, and an absolute due date. It enters the `PreShopPool`, a pure buffer queue with no built-in release logic. A release policy (LumsCor, SLAR, Draco, or ConWIP) monitors the pool and admits jobs to the `ShopFloor`, the central orchestrator that tracks WIP, fires lifecycle hooks, and maintains EMA performance metrics. At each `Server` — a SimPy priority resource — the queue is re-sorted before every dispatch by a priority policy (a dispatching rule such as SPT, EDD, ATC, or Focus). The `Router` acts as the stochastic arrival process, generating jobs and feeding them into the pool. `Runner` repeats the whole simulation across multiple seeds; `SimLogger` records events throughout.

```mermaid
graph TD
    Env["Environment<br/>clock + events"]
    Job["ProductionJob"]
    PSP["PreShopPool"]
    RP["Release Policy<br/>LumsCor / SLAR / Draco / ConWIP"]
    SF["ShopFloor<br/>orchestrator"]
    R["Router"]
    DR["Dispatching Rule<br/>SPT / EDD / ATC / Focus"]
    S["Server"]
    Runner["Runner<br/>multi-seed"]
    Log["SimLogger"]

    Job -->|enters| PSP
    PSP -->|released by| RP
    RP -->|admits to| SF
    SF -->|routes via| R
    R -->|orders queue with| DR
    R -->|dispatches to| S
    S -->|completion| SF
    Env -.drives.-> SF
    Env -.drives.-> S
    SF -.records.-> Log
    Runner -.runs many.-> SF
```

## Intralogistics

A `TransferOrder` is queued in the `FleetCoordinator`, the central orchestrator for all AGV missions. The coordinator assigns an `AGV` via pluggable dispatch and repositioning policies, plans its path across the `LayoutGraph` (a directed graph of nodes and arcs), and enforces node-capacity constraints via a `TrafficManager`. The AGV picks inventory from a source `Warehouse`, transits to the destination, puts it down, and then either recharges at a `ChargingStation` or parks at a `ParkingArea`. All order and time-series metrics are collected throughout.

```mermaid
graph TD
    Env2["Environment"]
    LG["LayoutGraph<br/>Nodes + Arcs"]
    WH["Warehouse<br/>inventory + bays"]
    TO["TransferOrder"]
    FC["FleetCoordinator<br/>orchestrator"]
    DP["Dispatch / Repositioning /<br/>Replenishment / Load-recovery policies"]
    TM["TrafficManager"]
    AGV["AGV<br/>AGVType + SpeedProfile + Battery"]
    CH["ChargingStation"]
    PK["ParkingArea"]
    M["Order + time-series metrics"]

    TO -->|queued in| FC
    FC -->|assigns via| DP
    FC -->|paths on| LG
    LG -->|guarded by| TM
    FC -->|commands| AGV
    AGV -->|picks/puts at| WH
    AGV -->|charges at| CH
    AGV -->|parks at| PK
    Env2 -.drives.-> FC
    Env2 -.drives.-> AGV
    FC -.records.-> M
```

## How the two domains connect

Both orchestrators — `ShopFloor` for production and `FleetCoordinator` for intralogistics — run as SimPy processes on the same `Environment` clock, so their events interleave naturally without any special coupling. You can combine them in a single simulation by passing the shared `env` instance to both. For the full API surface see the [API Reference](../api/index.md); for worked walkthroughs see the [Production Planning & Control guide](../guides/production-planning.md) and the [Intralogistics guide](../guides/intralogistics.md).
