# Native roadmap step 3: static heterogeneous server capacity

Roadmap step 3 adds one modeling capability: fixed interchangeable parallel
processing slots at each workstation. Step 1 bounded retention and step 2 managed
cancellation/failure remain available. Studio observation integration is step 4;
this work does not promote native to production or select portable wheel baselines.
Implementation starts from `29596aecbbd653713c0c0be5037b070deb15fc2a`.

## API and modeling value

Both public experimental entry points accept the same optional argument:

```python
from simulatte.experimental.native import Job, NativeProduction, NativeStream

jobs = tuple(Job(0, (0, 1), (2, 0.25), 10) for _ in range(4))
backend = NativeProduction(2, jobs, capacities=(2, 1), wip_cap=None,
                           policy="fifo", retention="bounded")
# Attach backend to the managed Environment as before.

with NativeStream(2, capacities=(2, 1), wip_cap=None, policy="fifo") as stream:
    for job in jobs:
        stream.add(job)
    stream.run()
    assert stream.result()["finished"] == [2.25, 2.5, 4.25, 4.5]
```

The first server processes two jobs concurrently and the second processes one.
With default `(1, 1)`, these completion times are `[2.25, 4.25, 6.25, 8.25]`.
This is a change in the modeled system's capacity, not a runtime speedup result.
ConWIP remains an independent shop-wide job-count bound. Utilization normalizes
occupied slot-time by the sum of server capacities, matching `ShopFloorKPIs`.

Capacity inputs are copied into read-only tuples. Primitive positive integer
counts and their sum must be below UINT32_MAX. Omission and all ones use the
legacy path. Configuration occurs before admission/positive advancement; it
cannot change during execution. No storage is allocated per slot. Occupancy
counters are now uint32; 300 simultaneous users and capacity UINT32_MAX-2 pass.

This does not add per-machine identities/speeds, dynamic capacity, setups,
breakdowns, preemption, repeated route servers, a new release policy or arbitrary
Python callbacks inside C. The existing FIFO/SPT and release model remain the scope.

## Semantics and commitments

SimPy `Resource._do_put` stops its put-queue loop after one request. Native keeps
one grant per arrival/release trigger even when several slots are free; filling
all free slots in one trigger would change simultaneous-event ordering.

The production oracle uses the existing external admission contract: run strictly
before arrival time, create that timestamp's arrival processes, then resume. It
uses unmodified production Server/ShopFloor/ConWIP actors and operation hooks for
actual starts. An initial test used an ordinary timeout producer and queue-entry
timestamps as starts; that is a different event interleaving/measurement. The
corrected oracle follows the frozen ProductionStream comparison contract. This
experiment does not claim general translation of arbitrary Python scheduling.

| Configuration | Full retention | Bounded retention |
|---|---|---|
| Unit slots | NSTA0001, native-production-v1 | NBST0001 inside NRST0001, native-production-bounded-v1 |
| Non-unit slots | NSTA0002, native-production-capacity-v1 | NBST0002 inside NRST0002, native-production-capacity-bounded-v1 |

Version 2 adds a little-endian u32 capacity before each server record. Full state
keeps its 108-byte header and uses 16-byte server records; bounded live state keeps
its 100-byte header and uses 32-byte server records. The bounded wrapper still
contains the completion-history hash, live state length/state and scalar snapshot.
The sweep independently decodes capacities and occupancy and compares them with
the Python resources at every observation boundary.

Different capacity vectors have distinct trajectory and reproduction commitments
before any arrivals. Provider projection and public identity composition retain
their outer envelope versions and carry the new nested codec. Allocation counters
account for wider occupancy and the optional capacity array; they are not semantic
state. The C create ABI and provider API 2 stay available. Additive CAPACITY_VERSION=1
guards parallel-slot requests; older API-2 companions remain usable for unit models.

## Validation

The final matrix uses the same frozen source dictionary in all ten cells, matching
core sources in the pure wheel and freshly compiling the companion for each ABI.
Normal integrity checks remain enabled. Installed probes run outside Git with no
runtime compiler, perform real Coordinator work, delete model source, and reproduce
from the store. No prototype module is required by an installed model.

| Host | CPython | Installed regressions per ABI | Parallel-slot requests + source-deleted reproductions per ABI |
|---|---|---|---|
| bigboss, macOS arm64 | 3.11, 3.12, 3.13, 3.14, 3.15 | 225 pass | 8 + 8 pass |
| strangelove, Linux x86-64 | 3.11, 3.12, 3.13, 3.14, 3.15 | 225 pass | 8 + 8 pass |

Each cell covers fixed/dynamic admission, FIFO/SPT and full/bounded retention.
Reproduction matches the complete fingerprint and observation artifact, using a
fresh attempt. Each also retains the original install/identity probes, four
execution-failure regressions and seven ABI/integrity rejection checks. CI now runs
the capacity probe and 79 capacity tests in its existing ten native cells.

Further evidence:

- 128 production scenarios per host, with 48 jobs each, quarter-grid/non-binary
  processing times, mixed `(3, 1, 2)` capacities, unlimited/ConWIP5, FIFO/SPT,
  warmup 0.3 and nine right-open boundaries. Starts, ends, dispatch, releases,
  completions and every scalar match exactly. Full/bounded scalar state agrees.
  Both hosts produce identical state and trajectory hashes for every scenario.
- Previous companion versus final companion: identical unit-capacity state hashes,
  projections, snapshots and full trajectories at 80 observation points across
  both dispatch and retention modes and unlimited/ConWIP3. This uses the new core
  with both installed companions, proving the old unit provider still works.
- ASAN/UBSAN on both images: the unchanged 123-test provider gate plus 79 capacity
  tests pass on each host. Each host runs 330 allocation-sweep cases: 111 induced
  failures and 219 successes across setup, constructor, admission, advance and
  projection. Mutating failures poison ownership; projection allocation failure
  is retryable; close and fresh-owner execution work. No sanitizer finding.
  Leak detection is disabled; this is not a Python/OS leak proof.
- Repository suite and lint/type/docs outcomes are recorded in results/summary.json.

The first Linux build rejected the server-count overflow guard under GCC's
`-Werror=type-limits`. The final guard checks the tuple's size_t length instead.
It preserves defensive size checking and compiles cleanly on both hosts. Earlier
macOS cells and linux-cp314/build.log are retained as draft evidence; only the
`*-final-cp*` cells describe the final source matrix.

## Bounded memory

Separate fresh processes lazily admit 16 jobs per batch, 65,536 total, using
capacities `(3, 1, 2)`, SPT and ConWIP5. Full and bounded snapshots match at every
sample and completion. No Python list retains the workload/handles.

| Retention | Native allocated bytes after 16 jobs | Native allocated bytes after 65,536 jobs |
|---|---:|---:|
| Full | 4,516 | 8,980,756 |
| Bounded | 6,796 | 6,796 |

Bounded process peak RSS stays at 36,585,472 bytes on macOS and 31,903,744 bytes on
Linux at every sampled size. Full peak RSS grows to 47,513,600 and 42,319,872 bytes
respectively. These are host observations, not portable memory guarantees. The
existing requirement remains: bound outstanding work/input lookahead as well as
native retention. ConWIP does not bound an overloaded pre-shop pool.

## Evidence and rerunning

Evidence lives under `prototypes/native_server_capacity/results`. summary.json and
verification.json identify final cells, sources, wheel hashes and evidence hashes.
The previous wheel is the unchanged macOS CPython 3.14 identity-helper wheel;
legacy/current-unit-provider.json are byte-for-byte equal. Historical prototypes
and their evidence were not edited.

Build the current pure core wheel, then run one new host/ABI cell:

```sh
uv build --wheel --python .venv/bin/python --out-dir prototypes/native_server_capacity/.build/core
.venv/bin/python -m prototypes.native_server_capacity.validate \
  --python 3.14 \
  --core-wheel prototypes/native_server_capacity/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --output prototypes/native_server_capacity/results/new-host-cp314
```

`parity.py` runs the wider production sweep. `memory.py --retention bounded|full
output.json` runs one memory process. `legacy.py` produces comparable unit evidence
with old/new companions. `faults.py` requires private ND_TEST_ALLOCATIONS images
built by the unchanged native_provider_projection/safety.py and ASAN preload.
Stock KPI arithmetic retains `-ffp-contract=off` in release and sanitizer builds.

## Next step

Proceed to roadmap step 4 when requested: integrate the packaged native provider's
observation with Studio, using the existing scalar/KPI contract and bounded query
cadence. Preserve experimental labeling, cancellation/failure status, reproduction
and observer invariance. A full native entity/event trace and automatic translation
of Python models remain separate work. Native v3 still follows a successful Studio
v2 release; there is no release, push or deployment-baseline change in this step.
