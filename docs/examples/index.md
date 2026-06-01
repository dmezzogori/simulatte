# Examples

Runnable reference galleries. Every release policy and dispatching rule appears
in a gallery you can run in the browser — click **▶ Run** on any code block
(the first run installs `simulatte` via Pyodide; later runs are instant). Plots
render inline beneath the text output.

| Gallery | Domain | Mechanisms covered |
|---------|--------|--------------------|
| [Dispatching — stateless](dispatching-stateless.md) | Production | SPT, EDD, ODD, MODD, CR, FCFS, WINQ |
| [Dispatching — parameterized](dispatching-parameterized.md) | Production | PST, S/RO, ATC, COVERT, Raghu-Rajendran |
| [Dispatching — system-state](dispatching-focus.md) | Production | FOCUS |
| [Release — workload control](release-workload.md) | Production | Immediate, LumsCor, SLAR, SLAR-Limit, Continuous Release |
| [Release — WIP cap](release-wip.md) | Production | ConWIP, DRACO |
| [Release — triggers & starvation](release-triggers.md) | Production | periodic / on-arrival / on-completion triggers, starvation avoidance |
| [Intralogistics](intralogistics.md) | Intralogistics | AGV fleet, warehouses, charging, replenishment, plots |

All source scripts live in [`examples/`](https://github.com/dmezzogori/simulatte/tree/main/examples).
