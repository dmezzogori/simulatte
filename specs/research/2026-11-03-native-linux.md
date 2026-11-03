# Linux native-asset validation

The declared native-bundle gate passes on Linux x86-64. Clean ELF builds load
through the ordinary managed backend, produce exact paired outcomes, reject a
real dependency loaded from outside the verified cache, retain active bundles
during concurrent execution/pruning, and reproduce after deleting live source and
evicting cached binaries. No production Python or C change was required.

This validates the current restricted scalar production provider on a second
platform. It does not package the provider, expand model coverage, enable remote
native worker slots or establish a Linux performance claim.

## Host and frozen inputs

- Strangelove: AMD Ryzen 7 3800X, 8 cores/16 threads, Linux x86-64.
- Ubuntu 26.04.1 LTS, kernel 7.0.0-42-generic, glibc 2.43.
- GIL-enabled CPython 3.14.4, GCC 15.2.0; ABI
  `cpython-314-x86_64-linux-gnu`.
- Isolated checkout:
  `/home/davide/Developer/simulatte-linux-gate-20261103-0bc4d39`.
  Shipped through a Git bundle from `0bc4d391fa13554893551670c3268c1f2e036137`;
  dependencies installed with `uv sync --locked --dev --all-extras` using the
  system interpreter. No existing project checkout or machine configuration changed.
- The gate pins 295 source files, aggregate SHA-256
  `0f5ebeb6b15a4b291223c12a981442c824597d54ae2e80fd4ae72470f019db43`.
  The supplemental isolated pytest launcher is retained and hashed with the
  verification evidence, separately from the gate's source manifest.

The fixture builder creates a new temporary project and compiles the engine and
CPython extension there. Its extension has an ELF `NEEDED` entry for
`dynamic_engine.so` and a relative loader search path. No Mach-O files or native
images from bigboss are transferred into this build. Exact compiler commands are
pinned in the unchanged builder source; ELF header/dependency information and
compiled file hashes are retained in the gate metadata.

## Correctness, real loading and lifecycle

The 96 main executions cover both independent and feedback producers, FIFO/SPT,
two paired seeds, production/native backends and three boundaries:
H1, H10/WU2, H200/WU20. Each runs through the ordinary experiment API, over two
local slots, with either per-attempt binaries or declared cached bundles.

All 48 pairs between loading paths match exactly in shared KPI values/windows,
admissions, steps and optional trajectory/input/controller proofs. Each backend
also preserves its own initial/final state, semantic fingerprint and deterministic
artifact hash. Production and native state encodings remain different schemas;
their shared outcomes are compared separately.

The Linux-specific probes establish:

- A fresh child loads the extension and its actual mapped engine dependency from
  the verified content-addressed directory. Bytes and file identities remain stable.
- Another fresh child first preloads the same engine bytes from captured source.
  Checked extension loading rejects that actual outside-cache mapping with
  `Native dependency loaded from an unexpected origin`. This is a real ELF loader
  probe, rather than a mocked path test.
- Four additional native feedback replications of 20,000 jobs use both
  `local-1` and `local-2`. The parent observes two simultaneous leases, then runs
  dry-run and actual pruning while holding the lease-manager lock. Both preserve
  the active entry; all four replications succeed and leases return to zero.
- Readiness preview cancels successfully before execution advancement.
- Corrupting the idle extension causes a fresh native reproduction to fail with
  `Native asset length differs from captured source`. Committed result count
  remains 52 before/after rejection; there is no automatic repair.
- Explicit pruning removes the invalid entry. After deleting live prototype
  source, fresh production and native reproductions rebuild from captured source
  and preserve their original fingerprints and artifact hashes.

The replayable audit checks all rows and lifecycle evidence. Deliberate corruption
of fingerprints, artifacts, backend state, KPIs, native origins, asset descriptors
and mapped-dependency rejection is rejected. It verifies 155 production Python/C
files and all 65 historical evidence files against `0bc4d39`. The previous macOS
results are preserved byte for byte.

## Regression checks and startup isolation

The focused backend/asset/prototype suite passes **548 tests, one skipped** in
52.89 seconds. The skip is the release extension's unavailable allocation-test
hooks. Existing asset tests exercise manifest/declaration/ABI validation,
invalidation, concurrent acquisition, active pruning, source/cache mutation,
publication failures before/after rename and abandoned staging.

The standalone GCC ASan/UBSan executable passes with leak detection enabled:
native state boundaries/lifetimes and pure failures, 90 stock mutating allocation
failure positions plus read-only failures, 143 dynamic positions, 143 rich-recording
positions and 144 bulk positions with 1,025 copied jobs. This instruments the
engine/helper; the ordinary CPython extension remains the release build. Linux
Ruff and `ty check src` also pass.

The broad core/execution/intralogistics/experimental/layout suite passes
**3,429 tests, four skipped**, in 413.82 seconds. Skips cover one macOS-pinned
libm golden test, two Studio packaging tests requiring unavailable Node, and the
native Windows Job API. Counts overlap with the focused suite. The result is
recorded in `linux-results/checks.json` and its JUnit XML. It uses the same
`-I -S` startup isolation as managed execution children, with the venv's packages
and verified repository runtime source added as plain import paths.

Normal Ubuntu Python startup imports `sitecustomize` through a stdlib symlink to
`/etc/python3.14/sitecustomize.py`. A pre-existing in-process inspection test audits
that already-loaded module and correctly rejects its origin outside the declared
roots. The normal-startup broad run therefore stops with one failure after 1,597
passes/three skips. The single test fails independently under normal startup and
passes under isolated startup. No import-policy exception, test alteration or
system Python configuration change was made. Managed children already use
`-I -S`; the 96-run gate passed with the ordinary coordinator.

The first clean-suite launcher was supplied on stdin, which multiprocessing could
not respawn as a real file. It timed out in the parallel Runner test. The retained
`isolated_pytest.py` has a real file entry point and a main guard; both the
inspection and parallel Runner regressions then pass together. Failed launch logs
are retained separately from the final broad regression result. Normal-startup
in-process test isolation on distro Python remains a follow-up outside this gate.

Two earlier setup/harness attempts are also distinguished from final results:
the historical benchmark-fixture test first required a companion PyPy extension
for its two-interpreter metadata inventory (built with the already-installed
interpreter; no PyPy timing/diagnostics were run). The first new native gate used
seed 200 for its extra concurrency case, while the reused result reader assumes
100+i. After correcting that new harness to the established seed range, the entire
96-run gate was regenerated from the final frozen sources. The draft empty gate
and log remain under remote ignored `.build/linux-before-seed-fix/`; no partial
draft data is reused as authoritative gate evidence.

## Evidence and reproduction

Authoritative files are under `prototypes/native_engine_dynamic/linux-results/`:
`gate/metadata.json`, `gate/runs.json`, `gate/lifecycle.json`, gate verification,
build/test/sanitizer logs, installed dependency inventory, startup diagnostics and
the isolated pytest launcher. Transfer and final verification records hash the
raw evidence; the gate can also be audited on bigboss against the same sources.
All 24 transferred files match their Linux hashes. A fresh local re-audit produces
the same certificate data as the Linux audit; the exact original Linux certificate
bytes are retained. The final master verification SHA-256 is
`326885be151e86fa57e70e33f7412a7f5ae4907048c71c47e94bd9f24efbf969`;
the Linux gate certificate SHA-256 is
`296e6aeed6f9a38dd71a5c3c4a79ab4de0f3f630e9ecc1263e7ca63ce0be207f`.
The remote checkout is retained as a reproducibility artifact, with the two new
research sources and validation outputs alongside its immutable base checkout.

| Clean fixture asset | SHA-256 |
| --- | --- |
| CPython ELF extension | `af85c6680be4f0414f825083954787bc3e7ec6201cf98d6e0cd90ba0102e2cbd` |
| Linked ELF engine | `bfd94b5e30a68c71fc273deaedca2d7693013049931b3ce89db38761d6a7b53a` |

Run on Linux from a checkout of this work, using new output destinations:

```sh
uv sync --locked --dev --all-extras --python /usr/bin/python3
.venv/bin/python -m prototypes.native_engine_dynamic.linux_gate gate --output /tmp/linux-native-gate
.venv/bin/python -m prototypes.native_engine_dynamic.linux_gate audit --output /tmp/linux-native-gate
.venv/bin/python -I -S prototypes/native_engine_dynamic/linux-results/isolated_pytest.py \
  tests/core tests/execution tests/intralogistics tests/experimental tests/layout \
  -o addopts='' -q --tb=short -ra
```

The gate builds its own fixture assets. The broader prototype tests additionally
need their usual static/dynamic default libraries and extension builds; their
recorded setup commands are in `build.log` and `checks.json`.

## Scope and next gate

This clears the Linux x86-64/glibc correctness/loading/lifecycle gate for the
restricted scalar provider on CPython 3.14.4. It does not certify Linux ARM,
musl, all Python ABIs, free-threading, arbitrary native dependencies, Windows or
SSH native worker slots. No performance matrix was run; test durations and the
four larger concurrency replications are not throughput measurements.

The next proposed step is optional experimental provider packaging with a small
usable Python entry point, clean-install checks and macOS/Linux ABI/platform CI.
That proposal is not an adopted commitment. Broader actors, policies, native
event tracing and remote workers remain separate work.
