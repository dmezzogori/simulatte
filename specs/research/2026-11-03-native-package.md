# Optional experimental native backend packaging

Date: 2026-11-03. Base: `97fc6339c3c777a00bb39e0e6731917ff60cb0ed`.
Worktree: `feature/native-engine-feasibility`. This is a local packaging gate;
no package publication, push or merge is part of it.

## Result and implications

The validated native engine is now available through a separately built
`simulatte-native` companion and `simulatte.experimental.native`. The core wheel
remains pure Python. Users can explicitly choose the native backend without
importing prototype files, compiling on each run, or constructing a model-specific
binary loader. A built wheel needs no runtime compiler.

Both macOS arm64 and Linux x86-64 pass clean-install checks with CPython 3.11,
3.14 and 3.15. Across these six cases, 48 managed runs have exactly equal seeded
KPIs across platforms/ABIs. All 24 fresh reproductions after deleting the model
source match their original run fingerprints within the same environment.
All 42 intentional invalid-install/runtime checks reject the affected run.
These checks establish packaging, API and provenance behavior for the supported
model scope; they are not a new performance measurement or broader feature claim.

## Distribution and public API

- `native/` is a complete independent source distribution. Its Hatch build hook
  builds the previously validated two-image C extension/engine pair. Five vendored
  C/header files match the prototype and base commit byte for byte. Only the stock
  KPI translation unit uses `-ffp-contract=off`, as in the validated build.
- The optional `native` extra selects `simulatte-native` on CPython/macOS/Linux;
  the `all` convenience extra excludes it. Development uses an explicit
  noneditable uv path dependency so the built companion is installed once.
- `Job` copies and validates primitive finite inputs. `JobHandle` is owner-scoped.
  `NativeStream` owns a native capsule and exposes `add`, right-open `advance`,
  `inspect`, sealing `run`, detached `snapshot`/`result`, state bytes and close.
- `NativeProduction` implements the ordinary managed `ExecutionBackend`
  lifecycle. Fixed jobs are registered eagerly, including future arrivals.
  Dynamic admission requires a pure `input_state` callback covering future Python
  input/controller/RNG state. Native state plus that callback are fingerprinted
  using the new `native-production-v1` schema. The framework publishes scalar KPIs
  only after successful finalization and verification.

```python
from simulatte.experimental.native import Job, NativeStream

with NativeStream(servers=2, wip_cap=2, policy="spt") as engine:
    engine.add(Job(arrival=0, route=(0, 1), durations=(2, 3), due=10))
    engine.run()
    print(engine.snapshot()["scalars"])
```

From this checkout, `uv sync --extra native` builds and installs the companion.
For independent installs, build the core and companion wheels, then install the
matching local pair. The current pair uses the repository's existing `0.17.0`
version; no companion is published by this work. `pip install 'simulatte[native]'`
becomes an external-user installation path only after a coordinated release.
See `docs/guides/native-engine.md` for direct and managed runnable examples.

## Installed binary integrity and ABI contract

The wheel contains the extension, namespaced `simulatte_native_engine` shared
library, and a manifest recording the provider API, exact core/provider version,
interpreter ABI/platform/GIL facts, source hashes and binary hashes/sizes.
The loader checks the manifest against the wheel RECORD, then validates current
ABI and bytes before and after loading. It verifies the extension origin and the
actual mapped engine dependency. Verification repeats at close, before managed
result publication. There is no silent Python fallback.

Installed wheel assets have stable distribution paths and use the installed
RECORD/manifest checks. Project-declared source bundles retain their separate
source-capture/cache/lease contract. This is trusted-code integrity/provenance,
not a security sandbox against a malicious process able to rewrite the runtime.

Wheels are CPython/platform-specific, neither universal nor `abi3`. Current local
macOS wheels carry the host macOS 27 arm64 deployment tag; Linux wheels carry a
host `linux_x86_64` tag and were built with glibc 2.43. Older macOS/glibc deployment
baselines and portable release wheels are not established by this gate.
Hatch's [wheel build data](https://hatch.pypa.io/latest/plugins/builder/wheel/#build-data)
provides platform-tag inference and forced binary inclusion; uv's
[cache keys](https://docs.astral.sh/uv/concepts/cache/#dynamic-metadata)
include compiler selection, sources and package metadata.

## Source-discovery integration fix

The first broad regression run exposed an existing assumption in
`local_package_roots`: every installed directory-origin distribution was treated
as editable source. A locally built, noneditable companion therefore appeared as
an external source dependency and caused 234 failures and three setup errors.
Explicit `tool.uv.sources` paths with `editable=false` also risked shadowing the
installed binary package with its unbuilt source tree.

The fix treats only `dir_info.editable=true` installed distributions as editable
source. Explicit uv noneditable paths remain validated for existence and project
containment, but do not become source import roots. Missing/false/null installed
editable flags denote noneditable dependencies; malformed flags still fail closed.
The [PyPA direct URL specification](https://packaging.python.org/en/latest/specifications/direct-url-data-structure/#local-directories)
defines a missing editable flag as false. Six focused snapshot regression cases
cover these boundaries. The clean-install managed probe also declares a deliberately
failing noneditable source stub: success proves the real installed wheel is used.
External editable dependencies still obey the existing containment rules.

## Clean-install evidence

Final source freeze: **180 files**, SHA-256
`52feb8cbdf988165ca7594e4280f60234fe996831e3086de299f6f5c257e7ccf`.
The same core wheel and source freeze were checked on both machines.
Raw evidence and hashes: `prototypes/native_engine_dynamic/package-results/`.
Run `python3 prototypes/native_engine_dynamic/package-results/verify.py` to audit
sources, recorded evidence, unchanged C/history and cross-platform KPI parity.

| Host | Build compiler | Installed CPython versions |
| --- | --- | --- |
| bigboss, macOS 27.2 arm64 | Apple Clang 21 | 3.11.13, 3.14.3, 3.15.2 |
| strangelove, Linux x86-64 / glibc 2.43 | Ubuntu GCC 15.2 | 3.11.16, 3.14.0, 3.15.0 |

Every platform/ABI case performs:

1. A standalone native sdist-to-wheel build, plus core pure-wheel/sdist checks.
2. A clean base-only install with actionable missing-provider error, followed by
   installing the native extra from the matching local wheel directory.
3. The direct Python example and eight two-slot managed runs: fixed/dynamic
   inputs × FIFO/SPT × seeds 100/101, with horizon/warmup and ConWIP.
4. Four fresh reproductions from captured source after deleting `plant.py`.
5. Seven negative checks: ABI mismatch, provider API mismatch, manifest/RECORD
   mismatch, binary corruption, genuinely preloaded wrong-origin shared library,
   binary mutation during a managed run, and a foreign CPython wheel tag.

Child probes run outside the checkout, with only Git on PATH and an invalid CC;
no runtime compiler or prototype imports are available. Runtime mutation produces
no published result. Binary, provider, installed dependency and runner/probe
hashes are retained. GitHub Actions now has the same six-target validation matrix
and retains wheel/evidence artifacts; the workflow has not been run remotely
because this work has not been pushed. It does not publish packages.

## Regression and checks

Fresh full suites: **3,554 passed, 1 skipped on macOS (99.05% coverage)**;
**3,551 passed, 4 skipped on Linux (99.02% coverage)**, with three existing
warnings on each. Linux skips the macOS/libm golden sample, two Node-dependent
Studio wheel checks (Node absent), and the Windows Job API. An earlier Linux
functional pass was 98.98%; the remaining native publication/load guards were
added and both full suites rerun cleanly.

Exact results and coverage counts are recorded in `checks.json` and
`regressions-*.log` beside the matrix evidence. The repository's existing 99%
line-and-branch coverage threshold is unchanged. Added tests exercise native
ownership, failed cleanup, publication guards, malformed manifests, unsafe cache
entries and file identity races, plus 45 public-API cases including 16 exact
comparisons with ordinary Python `ShopFloorKPIs` across policies, caps, partial
horizons and warmup. No tests merely reproduce the wheel build implementation.

Ruff lint and formatting for `src`, `tests`, `native` and the two touched guides,
type checking, lock consistency and the documentation build pass. The unchanged
repository-wide formatter command still flags 12 pre-existing research Python
files under `prototypes/native_engine_dynamic`; their exact bytes are retained.
This remains a known CI formatting failure to resolve separately, rather than
rewriting past experiment artifacts or weakening the formatter check. Six files from preceding native work needed formatting; their ASTs match
the base commit. The native C logic and prior raw experiment results remain
unchanged. Linux broad regression uses the existing isolated pytest launcher to
avoid the host's unrelated system `sitecustomize`; it retains a real script entry
point for multiprocessing and changes no managed import policy.

## Iterations and scope limits

Ignored draft evidence includes early probe harness mistakes, an AppleDouble
transfer artifact caught by the source manifest, the noneditable discovery failure,
and a matrix start without uv on remote PATH. Draft matrices before the source
fix/formatting are superseded. Final evidence was regenerated from the final
freeze, rather than relabeling earlier results. Portable Python tar transfer
avoids macOS sidecars. No rejected draft is counted as a passing final case.

Supported semantics remain fixed unit-capacity servers, unique-server routes,
FIFO/SPT, EDD ConWIP or immediate release, finite dynamic admission and eight stock
scalar KPIs. Managed execution requires Horizon, empty layout and KPI-only traces.
Generic `ShopFloor` actors/policies, arbitrary operation hooks, full event traces,
intralogistics, Windows, PyPy, free-threading and remote native slots are outside
this provider's validated scope. Completed native jobs remain retained until close.

The next proposed gate is coordinated release preparation: select a new matching
core/provider version, choose supported macOS/glibc baselines, build/repair portable
wheels and test those artifacts on the oldest supported systems before publishing.
This is a recommendation, not an adopted publication commitment.
