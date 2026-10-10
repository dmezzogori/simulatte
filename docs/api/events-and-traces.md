# Events & traces API

The observable core of Simulatte: events and the bus, entities, deterministic randomness, the semantic digest, provenance, traces and KPI collectors. For a conceptual tour see the [Events, traces and KPIs guide](../guides/events-and-traces.md).

The stable entry points are also exported from the top-level package: `Environment`, `Runner`, `Provenance`, `TraceRecorder`, `Trace`, `KPI`, `Collector`, `Event`, `DomainEvent` and `ObserverEvent`.

## Events

Typed events, state deltas and the event bus (`simulatte.events`). Subscribe with `env.bus.subscribe(handler, types)`; emit with `env.emit` behind an `env.wants` guard. The [Events, traces and KPIs guide](../guides/events-and-traces.md) explains the model.

::: simulatte.events.Event
    options:
      heading_level: 3
      members: false

::: simulatte.events.DomainEvent
    options:
      heading_level: 3
      members: false

::: simulatte.events.ObserverEvent
    options:
      heading_level: 3
      members: false

::: simulatte.events.event_type
    options:
      heading_level: 3
      members: false

::: simulatte.events.EventBus
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.events.Subscription
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.events.Deltas
    options:
      heading_level: 3
      members: false

::: simulatte.events.DeltaBuilder
    options:
      heading_level: 3
      members: false

::: simulatte.events.KpiSample
    options:
      heading_level: 3
      members: false

## Entities

Components that appear in events are entities with an id and a declared state schema (`simulatte.entities`); `env.entities` is the registry.

::: simulatte.entities.Entity
    options:
      heading_level: 3
      members: false

::: simulatte.entities.EntityRegistry
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.entities.StateSchema
    options:
      heading_level: 3
      members: false

::: simulatte.entities.FieldSpec
    options:
      heading_level: 3
      members: false

## Randomness

Named RNG streams and sampler binding (`simulatte.rng`); see `Environment.rng` and `Environment.bind`.

::: simulatte.rng.derive_seed
    options:
      heading_level: 3
      members: false

::: simulatte.rng.SamplerDescription
    options:
      heading_level: 3
      members: false

## Digest and provenance

The semantic digest, the fingerprint of a run and the run manifest.

::: simulatte.digest.SemanticDigest
    options:
      heading_level: 3
      members: false

::: simulatte.digest.Fingerprint
    options:
      heading_level: 3
      members: false

::: simulatte.provenance.Provenance
    options:
      heading_level: 3
      members: false

::: simulatte.provenance.RunManifest
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

## Traces

`TraceRecorder` writes a run to a trace file and `Trace` reads, seeks and verifies it (`simulatte.trace`).

::: simulatte.trace.TraceRecorder
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.trace.ChunkLimits
    options:
      heading_level: 3
      members: false

::: simulatte.trace.Trace
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.trace.ReaderLimits
    options:
      heading_level: 3
      members: false

::: simulatte.trace.TraceEvent
    options:
      heading_level: 3
      members: false

::: simulatte.trace.ChunkInfo
    options:
      heading_level: 3
      members: false

::: simulatte.trace.TraceCorrupted
    options:
      heading_level: 3
      members: false

## KPIs and collectors

KPI declarations, the `Collector` base class and observation windows (`simulatte.kpi`). The built-in collectors are documented under [Core](core.md#collectors) and [Intralogistics](intralogistics.md#metrics).

::: simulatte.kpi.KPI
    options:
      heading_level: 3
      members: false

::: simulatte.kpi.Collector
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.kpi.Window
    options:
      heading_level: 3
      members: false

::: simulatte.kpi.TimeWeighted
    options:
      heading_level: 3
      members: null
      filters: ["!^_"]

::: simulatte.kpi.observation_window
    options:
      heading_level: 3
      members: false
