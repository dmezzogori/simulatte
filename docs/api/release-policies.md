# Release Policies

Release policies control when jobs are released from the pre-shop pool onto the shop floor,
regulating work-in-process (WIP) and shop congestion. Simulatte ships several workload-control
policies — `Slar`, `SlarLimit`, `LumsCor`, `Draco`, `ConWIP`, and `ContinuousRelease` — along
with event triggers and a starvation-avoidance callback. For the underlying concepts and a
worked walkthrough, see the [Production Planning & Control guide](../guides/production-planning.md)
and the [Release control and dispatching tutorial](../tutorials/release-control-and-dispatching.md).

### Slar

::: simulatte.policies.Slar
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### SlarLimit

::: simulatte.policies.SlarLimit
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### LumsCor

::: simulatte.policies.LumsCor
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### Draco

::: simulatte.policies.Draco
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### ConWIP

::: simulatte.policies.ConWIP
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### ContinuousRelease

::: simulatte.policies.ContinuousRelease
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### Triggers

::: simulatte.policies.on_arrival_trigger
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

::: simulatte.policies.on_completion_trigger
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

::: simulatte.policies.periodic_trigger
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false

### Starvation avoidance

::: simulatte.policies.starvation_avoidance
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 4
      members: false
