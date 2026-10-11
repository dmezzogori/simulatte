# SP4 durable store foundation evidence

Task 6 foundations on eva/macOS. This does not establish the managed run CLI,
Studio integration, sender window or native Linux/Windows acceptance.

## Storage and failure checks

- A single OS owner uses SQLite WAL/FULL with explicit transactions. Actual owner
  process death releases the lock; a second live owner cannot open the index.
- Identical edits receive distinct revision numbers. Assignment replacement and
  revocation fence previous attempts transactionally. Failed assignment rolls
  back both supersession and request insertion.
- A 17 MiB segment receives durable partial upload credit before whole-segment
  visibility. Wrong, short, gapped and conflicting duplicate fragments fail.
  Finalizing closes segment writes; cancelled prefixes become inaccessible.
- Full trace publication requires semantic verification. KPI-only publication
  records `not_verifiable` while still checking structure, bytes, outcome,
  manifests and fingerprint equality. Actual scalar KPIs and model artifacts,
  including empty files, are indexed with the result.
- Filesystem work does not hold the assignment transaction. Cancellation during
  writes or finalization cannot publish. Receipt reads use batches of 64; a
  130-segment artifact crosses the batch boundary and publishes correctly.
- Real subprocess crashes at verification, staging, rename before directory sync,
  after rename, before commit, inside the SQL transaction and after commit show
  either no indexed result or one verified committed result. Recovery never
  adopts an unindexed directory.
- ENOSPC in staging/rename, an index write failure, short writes, symlinks,
  source replacement while copying, CRC-valid semantic corruption and mismatched
  proposal/receipt data cannot publish.

## Compatibility findings

The CPython 3.11 targeted suite passed 100 checks. Initial PyPy 3.11 testing found
an unread `PRAGMA journal_mode=WAL` result prevented the next COMMIT: discarded
cursors need not be finalized immediately on PyPy. Consuming the PRAGMA result
fixed it; the complete targeted suite then passed 100 checks.

One full CPython run before that cursor fix passed 2,597 tests at 99.03% coverage.
The final post-fix full run also passed 2,597 tests with one native-Windows skip
at 99.03% coverage. Ruff and ty pass. Native Windows and Linux checks remain in
Task 13; simulated Windows API checks are not equivalent.

## Primary sources and operating assumptions

- [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous):
  WAL/FULL provides the commit sync required for durable receipt/index visibility.
- [SQLite WAL](https://www.sqlite.org/wal.html): local filesystem only. The current
  documentation also describes a WAL-reset race fixed in 3.51.3 and selected
  backports. Eva's CPython runtime has SQLite 3.50.4. The store's single serialized
  writable/checkpointing connection avoids the documented multi-connection race
  trigger; that is an architectural inference, not a patched-version claim.
  Additional connections must use URI `mode=ro`, never write or checkpoint.
- [Python sqlite3 transaction control](https://docs.python.org/3/library/sqlite3.html#transaction-control-via-the-isolation-level-attribute):
  `isolation_level=None` and explicit SQL transactions support the Python 3.11 floor.
- [Python msvcrt locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking):
  the Windows owner locks and unlocks the same byte using a nonblocking lock.

Power-loss testing on physical platforms is not claimed. Crash tests terminate
processes; filesystem sync behavior is verified through ordering/failure tests.
