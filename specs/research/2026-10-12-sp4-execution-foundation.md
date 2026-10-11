# SP4 execution transfer and provenance foundation

Status: verified child/transfer foundation, not the Task 7 headless acceptance gate. Coordinator,
CLI/reproduction and complete runtime/snapshot preflight are still pending.

## Evidence on eva

macOS, CPython 3.14.1. Tests use actual fresh execution processes for build,
ready, cancellation and result transfer, and in-process fixtures for detailed
failure injection. No native Windows or Linux pass is inferred.

- Bounded sender and spool: 38 targeted tests, including a segment larger than
  the transfer window, cancellation without ACK and retry under queue pressure.
- Supervisor: 12 targeted tests, including a terminal proposal overtaking more
  than one poll batch of bulk frames and a saturated downstream queue.
- Driver/import/private control: 33 targeted tests. Fresh execution waits for
  durable INITIAL and resolved metadata before ready acknowledgement; its result
  passes the existing fenced publisher. Cancel at ready and invalid builds fail
  without entering running.
- Driver failure suite: 17 tests including artifact streaming, metadata drift,
  bad request identity, unknown KPI, advancing build, non-None/asynchronous build,
  process exception and artifact symlinks.
- Trace metadata and publication/KPI selection: 68 targeted tests. Filtering
  trace KPI declarations/series/scalars leaves collectors, common KPI values and
  domain digest unchanged.
- TypeScript workspace: type checks pass; trace 289, playback 21, scene 16 and
  export 14 tests pass. Viewer has no unit test files at this stage.

`tests/fixtures/traces/generated/execution_full.simtrace` and its expected JSON
are shared Python/TypeScript conformance fixtures. Existing generated and frozen
trace bytes were retained. The fixture regeneration check passes.

The released `simulatte==0.15.0` wheel, installed in an isolated uv environment
with CPython 3.11 and imported outside the checkout using `python -I`, successfully
calls `Trace.check()` and `Trace.verify()` on the new fixture (`verify is True`).
This is old-reader compatibility evidence for optional execution-v1 metadata,
not an installed 0.16 CLI/Studio smoke test.

The final full Python suite passes: **2,672 passed, one native Windows skip,
99.05% branch coverage**. Ruff lint/format and `ty check src` pass. The expanded
failures include malformed ACKs, disk-full classification, artifact mutation
around opening/reading/closing, publication provenance mismatches, bounded
progress fields and progress reporting independent from stepping.

The final driver/transfer/execution-metadata suite also passes on isolated
CPython 3.11 and PyPy 3.11: **54 tests each**.

## Remaining gate

Broader integrated driver faults,
metadata reference transport for inspection, snapshot/runtime verification,
coordinator and CLI/reproduction, live progress/KPIs and native platform evidence
must still pass. No success claim for complete SP4 is made here.
