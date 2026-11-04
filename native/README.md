# simulatte-native

Optional experimental scalar production backend for Simulatte. Install matching
core/provider wheels, or use the `native` extra once the pair is published:

```python
from simulatte.experimental.native import Job, NativeStream

with NativeStream(servers=2, wip_cap=2, policy="spt") as engine:
    engine.add(Job(arrival=0, route=(0, 1), durations=(2, 3), due=10))
    engine.run()
    print(engine.snapshot()["scalars"])
```

Requires GIL-enabled CPython on macOS or Linux. Wheels contain prebuilt binaries;
source builds need a C17 compiler and matching Python headers. No compilation or
downloads happen during import or simulation. Wheels are interpreter/platform
specific, not `abi3` or universal binaries. Build with `uv build native`; its sdist
is self-contained. Development uses a non-editable local wheel.

Fixed servers with configurable parallel slots, unique routes, FIFO/SPT, EDD ConWIP and eight scalar
KPIs are supported. The core package's experimental `NativeProduction` adapter
integrates this engine with managed Horizon execution, state fingerprints and
reproduction. Full tracing, arbitrary production actors, remote native slots,
Windows, PyPy and free-threading are outside this provider's supported scope.

`retention="full"` remains the default. Opt-in `retention="bounded"` reclaims
completed execution data at advance/run boundaries and expires its handles
explicitly. Live logical identities survive compaction. Full trajectory results
require full retention. Lazy input and bounded outstanding work are required for
bounded resident memory; ConWIP alone does not limit the pre-shop pool.

For unit capacities, full state remains NSTA0001/native-production-v1; bounded state is the distinct
NRST0001/native-production-bounded-v1 commitment. It includes a rolling history
hash and live/KPI state. Provider API 2 and matching core code are required.

`capacities=(2, 1, 3)` selects fixed identical parallel slots at each server.
Omission/all ones preserve legacy logical bytes. Non-unit vectors use NSTA0002
or NBST0002 inside NRST0002 and capacity-specific managed state schemas.
Each server record includes its capacity, and utilization divides by total slots.
Counts and their sum must be positive and below UINT32_MAX. The additive
CAPACITY_VERSION=1 feature requires rebuilding the companion; older API-2 wheels
remain usable for unit models. Capacity changes and per-slot machine identities
are outside this experimental API.

The additive experimental `state_projection()` hook on `NativeStream` and
`NativeProduction` requires companion projection v1 within provider API 2.
The provider atomically captures native trajectory material and stock reporting
state. Managed queries add scope/configuration and the existing full controller
authority under the owner lock. They return detached, explicitly versioned data;
consumers need no private owner access or binary-layout parsing. Trajectory alone
does not authorize reuse/reproduction, and full/bounded encodings remain distinct.
See the core native-engine guide for return fields and a Python example. Build and
reinstall matching local wheels to obtain this hook; no package release is implied.

The package shares validated event transitions through a private C engine
interface and separate retention/stock/binding translation units; see
`sources.json`. The build manifest pins ABI and binary bytes. Installed files are
verified against that manifest and its wheel RECORD, including actual linked
engine origin. These are integrity/provenance checks for trusted code, not a
security sandbox. Project-declared native bundles remain a separate mechanism.

This repository's current core/provider version pair is for local validation.
No companion release is published by building it. Coordinated versioning,
deployment targets and portable release wheels require the release process.
