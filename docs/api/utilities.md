# Utilities API

The builder functions are convenience wrappers that wire together an `Environment`, a
`ShopFloor`, a `Router`, and a release policy into a ready-to-run system, so you can spin up
a complete simulation in a single call. The distribution helpers sample processing times, with
`RunningStats` providing an online (Welford) accumulator for mean and variance. `SimLogger`
records simulation events and can emit them as JSON, plain text, or to a SQLite store.

## Builders

::: simulatte.builders.build_immediate_release_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_focus_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_lumscor_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_slar_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_slar_limit_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_draco_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_pure_job_shop_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_general_flow_shop_system
    options:
      heading_level: 3
      members: false

::: simulatte.builders.build_pure_flow_shop_system
    options:
      heading_level: 3
      members: false

## Distributions

::: simulatte.distributions.pure_job_shop_routing
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.general_flow_shop_routing
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.pure_flow_shop_routing
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.truncated_2erlang
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.arrival_rate_for_utilization
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.twk_due_date
    options:
      heading_level: 3
      members: false

::: simulatte.distributions.RunningStats
    options:
      heading_level: 3
      members: true

## Logging

::: simulatte.logger.SimLogger
    options:
      heading_level: 3
      members: true

::: simulatte.logger.LogEvent
    options:
      heading_level: 3
      members: true

::: simulatte.logger.EventHistoryBuffer
    options:
      heading_level: 3
      members: true

::: simulatte.logger.SQLiteEventStore
    options:
      heading_level: 3
      members: true
