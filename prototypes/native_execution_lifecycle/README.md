# Native cancellation and failure behavior

The installed gate advances one job to completion before each fault. It compares
full/bounded retention, cooperative Python-boundary cancellation, cancellation
during actual native work/admission, SIGTERM/SIGKILL escalation, timeout, late
publication fencing, producer/callback/input/advance/close failures and abrupt
child exit. A research watcher observes the binding's busy guard and attempts a
rejected close; a sleeping Python wrapper is not evidence of native execution.
Long native work uses a finite SPT queue without ConWIP; short work uses FIFO with
ConWIP1. This intentionally large live backlog is not a bounded-memory workload.

After every aborted request the same Coordinator executes a fresh healthy child
and compares its complete fingerprint/artifact with the initial healthy baseline.
Repeated abort/failure cycles, source-deleted reproductions, actual process and
descendant disappearance, finite parent RSS/descriptor ranges and zero successful
results for bad attempts are required. Stop timing ends at cleanup, not at the
store's immediate cancellation revocation.

Run with a newly built core wheel and the matching unchanged companion:

```sh
.venv/bin/python -m prototypes.native_execution_lifecycle.run \
  --core-wheel prototypes/native_execution_lifecycle/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel path/to/matching/simulatte_native-0.17.0-cp314-cp314-platform.whl \
  --output prototypes/native_execution_lifecycle/results/macos-release
```

`--quick` is preflight only. Build a private companion with
`CC='cc -DND_TEST_ALLOCATIONS' uv build native --wheel`, then run the same driver
with its wheel and `--instrumented`. Managed OOM requests and direct retry/poisoned
owner cycles use the same installed integrity checks. Separately run `direct.py`
and the existing provider tests with private ASAN/UBSAN images built by
`native_provider_projection/safety.py`. ASAN strips its preload environment on
macOS startup, so it is not propagated through the managed child chain.
Release wheels are never modified. Linux uses a new isolated checkout on
strangelove. Sources are frozen per final matrix; no competing same-host tests or
builds during stop-timing checks. See the plan/report for limits and failed drafts.
