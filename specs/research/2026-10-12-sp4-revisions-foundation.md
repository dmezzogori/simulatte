# SP4 revision and layout foundations

Status: implementation in progress; this is not SP4 acceptance or a release approval.

## Implemented boundaries

The coordinator owns an `InputOwner` containing immutable options, validation issues and the current revision. Apply validates data-only parameter schemas and normalized execution options before committing a revision. The revision and its captured preparation enter the run index in one transaction; invalid input leaves both unchanged. Seeds in input-state documents use canonical decimal text, preserving the full supported 63-bit range without JSON precision loss.

Source refresh invalidates provisional execution before capture. A latest-refresh token and revision comparisons fence both success and failure. Inspection and final-capture revision callbacks run under the coordinator ownership condition, so clients can observe an authoritative revision while preparation is underway. Project membership configuration is rediscovered without importing user code. Still-valid parameter values and same-family stopping values survive refresh; incompatible/removed parameters and a changed stopping family require explicit review through Apply.

Ready handling binds a verified durable INITIAL layout to its assigned inputs. Presentation reuse requires the exact current revision's view, equality of all non-layout execution inputs, and SP2 dependency-closure re-resolution. A presentation revision follows an already-running attempt through publication without changing its request, resolved manifest or trace bytes. Physical changes revoke the prior assignment and start a fresh attempt. An older committed view remains available separately.

Layout parsing accepts captured bytes directly. Reads reject symlink components, nonregular files, excessive bytes and observed file mutations. Saves validate and fsync a temporary file, commit an exact prior-byte backup separately, compare disk content immediately before replacement, and then commit the input/view revision. Backups use explicit base64 because the canonical wire value format does not admit binary values; an absent file remains distinct from an empty file. A post-rename failure pauses execution, or marks the coordinator unable to admit work when the index itself cannot record the failure. File replacement and SQLite are not one global transaction; an uncooperative external editor can still race the final comparison and rename.

The read-only watcher compares manifest membership and content hashes, observes declared non-Python inputs and Git metadata, supports native and polling modes, and rescans every five seconds. It bounds notifications and reports permission/errors without suppressing later recovery. Its implementation follows the primary [watchfiles API](https://watchfiles.helpmanual.io/api/watch/) and [filter documentation](https://watchfiles.helpmanual.io/api/filters/): `debounce=1000` is maximum batch age and `step=250` is the quiet interval. `watchfiles` is an optional Studio dependency and is imported only when watching starts.

## Verification record

The first full regression run passed 2,851 tests with one native Windows skip, but branch coverage was 98.54%, below the unchanged 99% gate. Added failure and concurrency tests raised accumulated coverage to 98.98% while production code remained fixed. A subsequent seed-boundary test exposed the input-state integer issue described above; that fix requires a fresh full run rather than appending to the earlier coverage data.

After the seed fix, a fresh full run passed **2,882 tests**, with one native Windows skip, one existing warning, and **99.03% branch coverage** in 283.07 s. The 67 targeted input/revision/file/watcher tests pass on macOS under both CPython 3.11 (97.50 s) and PyPy 3.11 (92.33 s), and on native Linux under CPython 3.14.0 (133.40 s). Ruff lint/format, repository-wide type checking and whitespace checks pass. No native Windows result is claimed here.

Linux used an isolated source copy on strangelove, without modifying its existing repository. The transferred source archive's SHA-256 was `89cee1cbafa95e727812a5cbc4e01d572597441d81f9b35bf9f0345e0af84511`; it contained the same tested production files and tests. Local logs are retained in the ignored SP4 work ledger. The targeted command was `uv run --all-extras pytest tests/execution/test_inputs.py tests/execution/test_layout_files.py tests/execution/test_revisions.py tests/core/test_studio_watcher.py --no-cov -q`; macOS compatibility runs additionally selected isolated CPython/PyPy 3.11 environments.

## Remaining integration

- Connect the watcher to a bounded latest-change queue and one refresh worker; acknowledge captured manifest baselines without losing edits during inspection.
- Move headless CLI input selection through the shared owner and retain reproduction behavior.
- Finish failure/coverage and native platform checks before treating Task 8 as complete.
- Implement Tasks 9–14: incremental live reader, authenticated Studio transport, parameter forms/full layout editor, history/export integration, acceptance and documentation/distribution.

No frontend live editing, complete Studio session, published package or complete platform acceptance is claimed by this foundation.
