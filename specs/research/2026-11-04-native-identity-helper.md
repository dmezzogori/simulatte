# Experimental identity helper: consolidation and compatibility gate

The prepared path is now available as
`simulatte.experimental.native_identity.compose_native_identity`. All ten actual
macOS/Linux CPython lanes pass. The helper preserves the frozen experiment's
canonical identity bytes, artifacts and default fingerprints. This establishes
integration compatibility for the existing native production slice; it does not
expand that slice or promote native v3 to production.

## API and behavior

```python
from simulatte.experimental.native_identity import compose_native_identity

# Inside a synchronized live NativeProduction controller boundary:
identity = compose_native_identity(backend.state_projection(), actions=actions.state())
payload = identity.to_dict()
encoded = identity.canonical_bytes
assert identity.size_bytes == len(encoded)
```

The caller supplies one fresh managed observation and its own wire-compatible
realized-action state. The helper prepares immutable values once per call, shares
canonical subtrees across the three component hashes, packs the complete envelope
and returns an immutable `IdentityComposition`. `to_dict()` makes fresh nested
containers each time. `native_bytes` preserves the provider's queried-state-size
diagnostic; it is not an allocation estimate.

The schema remains `native-identity-set-v1`, with unchanged trajectory/control,
reporting and reproduction component schemas. The full backend authority and its
preimage remain in the evidence. Future callback-only state changes still alter
authority/reproduction without altering realized trajectory/reporting. Action and
reporting changes retain their separate component effects.

The helper consumes data: it does not query/poll a backend, cache across queries,
invoke a callback, parse native layouts or import research code. Retaining a result
retains a snapshot. The public provider hook still performs two fresh callback
reads with the same synchronization, mutation and lifecycle guards. Neither C nor
provider runtime code changed. Default fingerprints, reuse and pinned Coordinator
reproduction are unchanged. The helper checks structure and wire compatibility,
not the truth of arbitrary provider observations or completeness of a controller.
It is not an engine restore or independent reuse-authorization API.

## Actual platform/version matrix

Every cell built its own matching companion wheel and installed the same new pure
core wheel in a clean environment outside the repository, with no runtime compiler.

| CPython ABI | macOS arm64 | Linux x86-64 |
| --- | --- | --- |
| 3.11 | 3.11.13 — pass | 3.11.16 — pass |
| 3.12 | 3.12.8 — pass | 3.12.12 — pass |
| 3.13 | 3.13.9 — pass | 3.13.9 — pass |
| 3.14 | 3.14.3 — pass | 3.14.0 — pass |
| 3.15 | 3.15.2 — pass | 3.15.0 — pass |

Hosts: bigboss, macOS 27.2 arm64; strangelove, Linux 7.0.0-42-generic x86-64.
Build compilers and interpreter details are retained in each host's metadata.
These are GIL-enabled ABIs. Testing the listed patch releases does not claim
coverage of every patch release, free-threading, PyPy, Windows or SSH native workers.
CI now retains the same five-ABI native install matrix on macOS and Linux.

Each lane passed:

- Base-only import/optional-dependency behavior, native-extra installation and
  direct stream execution, with no compiler on the runtime path.
- Real full/bounded provider queries against frozen prepared, unprepared and
  independent binary-parser consumers: exact canonical bytes and size diagnostics,
  unchanged callback-read count, detachment and callback-only future-state changes.
- Sixteen direct model runs: fixed/coupled feedback controllers × full/bounded
  retention × all four consumers, querying at every control poll. Every artifact,
  including compressed proof streams, and default fingerprint matched exactly.
- Four helper Coordinator requests and four fresh reproductions after deleting
  every project Python source. Artifacts and complete default fingerprints matched.
- The existing eight-request install gate, its four source-deleted reproductions,
  and all seven ABI/API/manifest/binary/dependency/runtime-mutation/wheel-tag
  rejection paths.

Across ten lanes this is 160 direct model oracle runs, 120 original Coordinator
requests, 80 source-deleted reproductions and 70 negative install probes. The audit
also compares query-byte hashes, every model artifact and full default fingerprints
across both hosts/all five ABIs; they are exact. The old eight-request fixture's
seeded fingerprints/KPIs also match across hosts and ABIs.

## Other verification and preserved evidence

- 174 focused tests pass on macOS and Linux, including 25 new helper cases.
- Full macOS suite: 3,651 passed, one skipped, three dependency warnings;
  99.06% branch coverage. The new helper has 100% statement/branch coverage.
- Linux private ASAN/UBSAN build: 123 native/provider projection tests pass;
  allocation failure followed by successful reuse passes in both retention modes.
  Python leak detection is disabled; this is not a Python leak proof.
- `ruff check src tests native`, formatting for those directories/new fixture
  files, `ty check src`, documentation build and `uv lock --check` pass.
- Whole-repository formatting still flags 12 untouched historical
  `native_engine_dynamic` research files. The audit confirms they match the base;
  they remain a separate existing CI formatting follow-up. No frozen file was
  reformatted to satisfy that broad check. An exploratory unscoped `ty check`
  also reports existing research/test/example diagnostics; the CI-scoped `src`
  type check passes.

The 199-file matrix source freeze is
`acecc121d52e4b3b1c61694f219ed9d409ec06b4358b6e0e3b7b44a062ccc696`.
The common core wheel is
`a57c155a1528976cc8ab76ad589cb692b410b4607373dc15eede1fcacf2a21d6`.
The macOS cp314 companion is byte-identical to the preceding experiment's wheel.
Wheel hashes, source maps, runtime versions, rejection errors, per-lane evidence
certificates and the combined audit are under
`prototypes/native_identity_helper/results/`.

All six historical model/consumer/parser files are used byte-for-byte, copied only
into external validation projects. Of the 4,124 base files, 4,119 remain unchanged;
the five intentional edits are CI, two documentation pages and two install/matrix
runners. Native C, provider runtime, backend API, historical prototypes and all old
evidence remain unchanged. New helper, tests and validation files are additive.

Preflight harness failures were retained: passing a Horizon instead of its request
dictionary, assuming a wheel origin for a core module captured in the trusted
runtime, and serializing a Fingerprint dataclass without conversion. They were
corrected before the final matrix; product helper bytes did not change. The first
Linux sanitizer invocation needed its private build parent directory created;
the subsequent complete run passed without changing the historical safety script.
A transfer initially replaced an old loose Linux wheel copy; it was restored from
the saved packaging copy, and current inputs/builds moved into dedicated paths.
The pre-existing remote checkout and its untracked files were left unchanged.

## Implications and next step

Models can now use the prepared composition without carrying the research
implementation or understanding the native serialization. The earlier experiment
measured about 18% lower full-query cost; this gate establishes byte/behavior/ABI
compatibility of the public helper, not a new timing result or universal speedup.
Full-retention native hashing still grows with history, and controller callbacks
still need complete future-state coverage. Native feature limits are unchanged.

A suitable next experiment is a paired end-to-end comparison through this public
API in a representative v3 model at realistic observation rates. It would check
that the public result-object/detachment choices retain the measured benefit and
identify whether observation or native hashing now dominates. This is a proposal,
not further work performed here. No deployment baseline, portable-wheel release,
push, publication or production promotion is part of this gate. Studio v2 remains
the release priority before native v3.
