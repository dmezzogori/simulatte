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

Fixed unit-capacity servers, unique routes, FIFO/SPT, EDD ConWIP and eight scalar
KPIs are supported. The core package's experimental `NativeProduction` adapter
integrates this engine with managed Horizon execution, state fingerprints and
reproduction. Full tracing, arbitrary production actors, remote native slots,
Windows, PyPy and free-threading are outside this provider's supported scope.

`retention="full"` remains the default. Opt-in `retention="bounded"` reclaims
completed execution data at advance/run boundaries and expires its handles
explicitly. Live logical identities survive compaction. Full trajectory results
require full retention. Lazy input and bounded outstanding work are required for
bounded resident memory; ConWIP alone does not limit the pre-shop pool.

Full state remains NSTA0001/native-production-v1; bounded state is the distinct
NRST0001/native-production-bounded-v1 commitment. It includes a rolling history
hash and live/KPI state. Provider API 2 and matching core code are required.

The package shares validated event transitions through a private C engine
interface and separate retention/stock/binding translation units; see
`sources.json`. The build manifest pins ABI and binary bytes. Installed files are
verified against that manifest and its wheel RECORD, including actual linked
engine origin. These are integrity/provenance checks for trusted code, not a
security sandbox. Project-declared native bundles remain a separate mechanism.

This repository's current core/provider version pair is for local validation.
No companion release is published by building it. Coordinated versioning,
deployment targets and portable release wheels require the release process.
