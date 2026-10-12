# SP4 spool foundation evidence

Scope: Task 5's recorder/sink/spool foundation. Transfer credit, durable receiver
receipts and run publication remain Tasks 6–7. This is not SP4 acceptance.

## Local measurements

Eva, macOS arm64 Apple M1 Pro, CPython 3.14.1. Compared the committed writer at
`2bc8c00` with the sink refactor, same interpreter and dependencies. Existing
`benchmarks/run.py`, `jobshop10-u90-5k.json`, full recording, one warmup and three
timed runs per variant, no memory or seek measurements. Runs were sequential.

| Variant | Median | IQR |
| --- | --- | --- |
| Before sink abstraction | 3.258 s | 0.030 s |
| FileSink | 3.286 s | 0.026 s |

Observed difference +0.8%. Informational small sample, no new performance gate
or claim about durable spooling overhead. Raw local logs and JSON are in the
ignored `.superpowers/sdd/2026-10-11-sp4-studio/` ledger directory.

## Behavior and failure evidence

- Full and KPI segment concatenations equal ordinary file traces byte for byte
  with fixed volatile metadata, including logical boundaries and reader verify.
- Existing writer/reader/fixture/invariance regression suite: 143 passed.
- Final full suite: 2,502 passed, one native-Windows skip, 99.02% coverage.
  Ruff and ty pass. The initial full run passed tests but failed coverage at
  98.70%; added failure-path coverage clears the unchanged 99% gate.
- Spool, sink and durability suite: 43 passed on macOS CPython 3.11 and PyPy 3.11.
- Quota covers active temporary reservations; cancellation interrupts credit
  waits; continuing ACKs permit slow consumption without dropping records.
- Short writes, ENOSPC, fdopen/fsync/rename failures, directory-sync failure after
  rename, failed cleanup, failed ACK deletion and duplicate/out-of-order/wrong
  ACKs are exercised. Failed deletion retains quota until cleanup succeeds.
- Oversize strict admission includes latency-sealed records. Plain file output
  retains the existing oversize/interruption policy.
- Authenticated segment ids validate duplicate historical ACKs after 1,100
  reclaimed segments without retaining receipt history in memory.

Windows native validation is pending. Local tests verify Unicode/UNC paths,
WRITE_THROUGH flags and failure handling against the documented API shape;
they do not establish crash durability on Windows hardware.

## Primary documentation

- [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw):
  same-volume replacement with WRITE_THROUGH; no copy/delete fallback requested.
- [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) and
  [WAL](https://www.sqlite.org/wal.html): forthcoming receiver index uses WAL/FULL
  on a local filesystem; browser receipt is never durable storage acknowledgement.
