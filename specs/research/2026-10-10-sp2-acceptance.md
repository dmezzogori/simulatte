# SP2 layout acceptance

Date: 2026-10-10. Measured product code: `6a3f131d8eec99d6552ed4c5ddf5807a3beea104`,
on `feature/sp2-layout`. Package metadata still says **0.13.0**; these measurements include the unreleased
SP2 implementation and are not measurements of the published 0.13.0 release. Task 10 adds documentation,
acceptance tests and measurement helpers; it changes no library implementation or performance budget.

Final task review, broad branch review and the final evidence/handoff update belong to the root agent.
No merge, push, release tag or package version bump is claimed here.

## Environment and command evidence

Local host: **eva**, MacBookPro18,1, Apple M1 Pro, 10 logical CPUs, 16 GiB RAM, macOS 27.0.1 (26A434), arm64.
CPython is **3.14.1**, Clang 21.1.4. PyPy is **7.3.18 / Python 3.11.11**
(`b38de282cead`, February 5, 2025, Apple LLVM 15.0.0). This is not the historical Linux PyPy 8.0.0 /
Python 3.11.16 benchmark environment. No unexecuted CPython 3.11–3.13/3.15 CI lane is claimed.

Tools: uv 0.13.0, Ruff 0.16.10, ty 0.0.85, pytest 9.1.1, Zensical 0.0.68, Node 24.13.0, pnpm 11.5.1,
SimPy 4.1.2, MessagePack 1.2.3. The commit hook retains its configured Ruff version; no hook configuration changed.
[Environment capture](sp2-acceptance/environment.json),
[every command/status/duration and saved output path](sp2-acceptance/commands.json), and
[raw outputs](sp2-acceptance/logs/metadata.txt) preserve exact local evidence. Durations below are command wall
seconds; individual tools also report their own inner durations in the logs.

| Check | Result | Command wall time |
|---|---|---:|
| `uv run --all-extras ruff check src tests` | Passed | 0.16 s |
| `uv run --all-extras ruff format --check` | Passed, 313 files | 0.04 s |
| `uv run --all-extras ty check src` (no ignored rules) | Passed | 0.21 s |
| `uv lock --check` | Passed | 0.02 s |
| Repository-wide hook ty command | Passed | 0.26 s |
| Focused integrated acceptance | 4 passed | 1.27 s |
| Existing docs block/link tests plus layout acceptance | 8 passed | 1.21 s |
| Docs runnable extractor | Passed | 0.22 s |
| Pyodide against freshly built docs wheel | **26/26 blocks passed**, including 7 new layout blocks | 24.32 s |
| `uv run --all-extras zensical build --clean` | Passed, no issues | 2.28 s |
| Built internal links | Passed, 53 pages | 0.07 s |
| `pnpm --dir studio test` | **261 passed**, 8 files | 1.49 s |
| `pnpm --dir studio typecheck` | Passed | 1.35 s |
| Isolated core-only PyPy CI paths, now including `tests/layout` | **2133 passed, 5 skipped** (74.35 s inner) | 75.56 s |
| Full CPython pytest with existing 99% combined coverage gate (branch measurement enabled) | **Pending the unchanged signed-commit hook** | Pending |

The full Python suite is deliberately reserved for the signed implementation commit's unchanged hook.
Its actual result and coverage will be recorded in the final documentation-only evidence update after review;
there is no duplicate manual full-suite run just to put a result into the commit that first produces it.

The five existing PyPy skips were confirmed with a targeted `-rs` run, without excluding anything from the
full PyPy command: `test_retire_is_atomic_for_kinds_without_weak_references` (PyPy objects support weakrefs),
`test_experimental_export_with_rl_extra` (Gymnasium absent), `test_numpy_scalar_priorities_are_numbers`,
`TestWireFloat.test_numpy_scalars_convert_and_nothing_else_from_numpy`, and
`TestWireFloat.test_numpy_times_and_complex_numbers_are_not_numbers_for_events` (NumPy absent).
[Exact skip reasons](sp2-acceptance/logs/pypy-skip-reasons.txt).

All unsuccessful intermediate commands are retained, not hidden. The initial RED test lacked the new guide;
the first guide execution caught an omitted explicit obstacle rotation; Ruff then formatted new Markdown
Python fences. The first PyPy run failed two new tests because the helper imported `tracemalloc` at module load
and this PyPy lacks `_tracemalloc`; the import now belongs only to the CPython preparation probe. The hook ty
preflight required an explicit non-None assertion for a successful presentation result. Final relevant checks
pass after these helper/test/documentation corrections. None was a library defect or a test exemption.

## Integrated and cross-runtime behavior

[`tests/layout/test_acceptance.py`](../../tests/layout/test_acceptance.py) combines file-controlled stage
counts, overrides on late-created ids, orphan retention, generated geometry, observer invariance, full trace
verification, stored-layer presentation reuse, and custom resource binders rejecting undeclared or auto reads.
It also executes all seven independent browser-tagged guide blocks natively. Existing layout/core/intralogistics
and TypeScript suites retain the detailed adversarial geometry, lifecycle and trace coverage.

[`benchmarks/layout_acceptance.py`](../../benchmarks/layout_acceptance.py) exports a core-only model with a
five-node lattice, a distant rotated obstacle, file-driven stage counts, one AGV transfer and FleetKPIs.
It asserts finite nondegenerate rectangle geometry and expected side lengths, required path resolution and
completed delivery. It exports all three layout hashes, full network/obstacle geometry, timed AGV trajectories,
semantic digest, KPI scalars, stage-created ids and diagnostics.

**42 fresh processes** ran: CPython/PyPy × rotations 0°, 90°, 180°, 270°, 13.7°, 27.3°, 45° ×
`PYTHONHASHSEED=0,1,42`. Every process exited zero. All exported values were identical within each runtime
across hash seeds. Quadrantal fixtures were identical across runtimes, including geometry, three hashes,
trajectories, digest and KPIs. For the three arbitrary angles, these two local runtimes also produced
**no differing transformed coordinates or hashes**. This observation does not establish cross-platform portability for
arbitrary-angle libm transforms. Actual coordinates, hashes, versions and comparisons are preserved
in [runtime-fixtures.json](sp2-acceptance/runtime-fixtures.json), rather than just recording an equality claim.

Reproduce from the repository root (the command ledger preserves the actual isolated interpreter path):

```sh
SP2_PYPY_ROOT=$(mktemp -d /tmp/simulatte-sp2-pypy.XXXXXX)
UV_PROJECT_ENVIRONMENT="$SP2_PYPY_ROOT/venv" uv sync --dev --python pypy-3.11 --frozen
MPLBACKEND=Agg "$SP2_PYPY_ROOT/venv/bin/python" -m pytest tests/core tests/intralogistics tests/layout -o addopts="-q -p no:cacheprovider"
PYTHONHASHSEED=0 .venv/bin/python benchmarks/layout_acceptance.py fixture --rotation 90
PYTHONHASHSEED=0 "$SP2_PYPY_ROOT/venv/bin/python" benchmarks/layout_acceptance.py fixture --rotation 90
```

Repeat the last pair for seeds 1/42 and each listed angle, comparing the JSON `result` object; runtime/platform
metadata is intentionally separate. The artifact retains one full result per runtime/angle plus all tested
hash seeds; the command logs also retain every individual fresh-process export.

## Preparation and encoded INITIAL size

Preparation is measured separately from run overhead. Inputs are deterministic, 4-neighbor unit lattices:
40×25 = **1,000 nodes / 3,870 directed arcs**, and 100×100 = **10,000 nodes / 39,600 directed arcs**.
Bounds are inclusive `(0, 0, nx-1, ny-1)`, grid spacing 1 m, seed 17, no obstacles, consumers or custom stages.

Each size has three fresh CPython processes with `gc.collect()` before `perf_counter()` around **only
`layout.prepare()`**, excluding declarations, activation, encoding and file I/O. Peak Python allocation is a
fourth separate process with `tracemalloc` around that same preparation call; its instrumented time is not
mixed into the timing sample. No other agent-launched CPU-heavy acceptance or benchmark job overlaps these probes; ordinary desktop
activity was uncontrolled. Whole-process
maximum RSS, including subsequent encoding, is retained in raw data and is not mislabeled as preparation-only
memory. No new preparation release budget has been adopted.

| Nodes | Prepare seconds (3 runs) | Median | Prepare peak traced allocation | Encoded layout bundle | Raw INITIAL payload |
|---:|---|---:|---:|---:|---:|
| 1,000 | 1.992906, 1.959064, 2.074578 | **1.992906 s** | **50,792,911 B** | 4,288,615 B | **4,491,360 B** |
| 10,000 | 20.810396, 20.955151, 20.997534 | **20.955151 s** | **507,194,846 B** | 43,406,217 B | **45,448,562 B** |

The memory probes take 13.87/140.51 seconds during preparation (instrumentation overhead); their whole-process
maximum RSS is 166,330,368 / 1,244,495,872 bytes. Use the uninstrumented samples for time comparisons.
[Raw preparation results, all hashes and process metadata](sp2-acceptance/preparation.json).

After each probe, a real `TraceRecorder` records activation. The helper reads the **INITIAL payload length
from its actual binary record framing** (`PREAMBLE`, `RECORD_HEADER`, `RecordType.INITIAL`); this excludes framing
and is neither compressed trace size nor JSON size. The bundle column separately uses the writer's wire
`pack(bundle.to_dict())`. Both sizes fit the unchanged default 64 MiB INITIAL limit. Each size's hashes and
payload byte count match across all four processes.

```sh
.venv/bin/python benchmarks/layout_acceptance.py prepare --nodes 1000
.venv/bin/python benchmarks/layout_acceptance.py prepare --nodes 10000
.venv/bin/python benchmarks/layout_acceptance.py prepare --nodes 1000 --memory
.venv/bin/python benchmarks/layout_acceptance.py prepare --nodes 10000 --memory
```

Run each non-memory command three times in fresh processes, sequentially without other heavy work. Memory
probes require CPython's `tracemalloc`; fixture execution remains compatible with PyPy.

## Existing benchmark gates and accepted limitation

Both complete current CI matrices and README report-only commands finished against the fixed source above.
**All eight no-subscriber gates passed**, retaining their exact budgets and noise bands. Each lane ran all
30 canonical measurements, including separate memory probes; no workload, mode or comparison was dropped.

| Runtime | Workload | Released overhead / allowed | Stripped overhead / allowed | Gate exits |
|---|---|---:|---:|---|
| CPython 3.14.1 | u90 | −27.40% / 3%+2% | +5.77% / 6.5%+2% | 0, 0 |
| CPython 3.14.1 | u95 | −25.87% / 3%+2% | +6.25% / 6.5%+2% | 0, 0 |
| PyPy 3.11.11 | u90 | −38.99% / 3%+7% | −20.79% / 3%+7% | 0, 0 |
| PyPy 3.11.11 | u95 | −34.03% / 3%+7% | −19.20% / 3%+7% | 0, 0 |

Report-only recording ratios (kpi / digest / full versus none) were **3.4598× / 3.0962× / 5.1556×** on
CPython and **3.9055× / 3.8116× / 6.1984×** on PyPy. All six are below the historical report-only targets
(3.7× / 3.2× / 5.3× and 4.5× / 4.3× / 7×). These are observations, not additional release gates.
The complete job-shop, intralogistics, sampling, replication, calibration and TypeScript trace reports all
exited zero. Replication trajectories matched on each runtime; calibration changed no noise band.

[Detailed ratios, medians, calibration and limitations](sp2-acceptance/benchmarks/report.md),
[exact reproduction commands](sp2-acceptance/benchmarks/command-ledger.md), and
[all 142 individual invocation statuses](sp2-acceptance/benchmarks/commands.jsonl) are durable.
The only nonzero invocation was an initial CPython base installation (exit 2): the local setup wrapper mixed
uv informational stderr into constraints. A quiet freeze corrected it before any measurements; both the
failure and successful setup are preserved. No timing was rerun. Six interpreter/import assertions and the
[artifact audit](sp2-acceptance/benchmarks/artifact-audit.txt) passed.

Benchmarks used isolated non-editable head/released environments with identical shared dependencies and a
separate stripped copy. Other agent-launched heavy work did not overlap measurements. Ordinary desktop
activity was uncontrolled; a process snapshot included Ghostty around 51% CPU. Raw samples and spread are
retained, so the local machine/runtime bounds remain visible.

[Issue #59](https://github.com/dmezzogori/simulatte/issues/59) remains the explicitly accepted PyPy recording
overhead limitation documented in the [0.13 release checks](2026-10-10-013-release-checks.md). Its newer PyPy
8.0.0 / Python 3.11.16 environment was not measured here. Recording targets remain report-only; the local
results are neither an issue fix nor test exemptions or permission to widen a gate/noise band. SP2 makes no
unrelated #59 fix or runtime pin.

## Wheel, sdist and runnable examples

`uv build --out-dir ...` successfully built an sdist, then a wheel **from that sdist**. Both archives contain
`layout/schema/layout-v1.schema.json`; both independently installed into fresh CPython 3.14 core-only virtual
environments. Smoke tests ran outside the checkout under `python -I` with `PYTHONPATH` cleared and asserted
that imports resolve under their own environment prefix. Installed requirements were only SimPy 4.1.2,
tabulate 0.10.0, tqdm 4.70.1 and MessagePack 1.2.3 plus Simulatte.

Each install imported core/layout/intralogistics/trace, loaded and parsed the schema through `importlib.resources`,
asserted matplotlib/NumPy/Gymnasium/jsonschema absent, executed all **eight** actual guide Python snippets,
and verified a generated layout/full-trace round trip with `Trace.check()` and `Trace.verify()`.
[Artifact SHA-256s, schema paths/hashes, import prefixes and installed requirements](sp2-acceptance/packages.json).
The seven browser-tagged examples also passed the independent all-documents Pyodide gate. The eighth is a
native trace example because the recorder starts a Python writer thread.

```sh
SP2_DIST_ROOT=$(mktemp -d /tmp/simulatte-sp2-dist.XXXXXX)
uv build --out-dir "$SP2_DIST_ROOT/dist"
uv venv "$SP2_DIST_ROOT/wheel-env" --python 3.14
uv pip install --python "$SP2_DIST_ROOT/wheel-env" "$SP2_DIST_ROOT"/dist/simulatte-*.whl
uv venv "$SP2_DIST_ROOT/sdist-env" --python 3.14
uv pip install --python "$SP2_DIST_ROOT/sdist-env" "$SP2_DIST_ROOT"/dist/simulatte-*.tar.gz
```

From a directory outside the checkout, run each installed interpreter with `-I`, the absolute path to
`benchmarks/layout_acceptance.py`, `package --guide` and the absolute path to `docs/guides/layout.md`.
The exact invocations and outputs are in the command manifest. Internal link checking does not validate
external GitHub links or anchors; the schema link names the canonical packaged source, whose actual archive
presence and resource loading were independently verified above.
