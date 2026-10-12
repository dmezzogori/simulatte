# SP4 authenticated local transport — implementation evidence

Date: 2026-10-12. Branch: `feature/sp4-design`. This is transport evidence, not
complete SP4 acceptance or release approval.

## Implemented boundary

The shared SP3 authentication/bootstrap implementation now also serves Studio.
`simulatte studio` binds loopback, starts the existing coordinator and watches
before initial inspection. Source errors and missing declared inputs can recover
without restarting. Missing required parameters retain a draft; invalid startup
overrides require Apply and remain blocked across source refreshes.

The versioned websocket transport enforces first-message authentication, origin
and host checks, framing/depth limits, socket quotas, bounded sends, command
idempotency and exact revision/attempt cancellation. Initial state precedes
notifications observed during its read. Attempt notifications retain the original
execution revision. Queue overflow and shutdown interrupt a blocked network send;
closing only the queue was insufficient and a regression test demonstrated it.

HTTP exposes authenticated immutable JSON references and committed trace prefixes.
Active prefix reads use sealed segments; indexed results use immutable final
files. Ranges cannot exceed their advertised generation and cancellation revokes
provisional access. Layout PUT shares revision fencing and protected replacement
with the existing input owner. The browser transport reconnects without replaying
mutations and distinguishes an expired server capability from a network outage.
Layout reads use captured revision bytes. Large finite float parameters preserve
their JSON float type; nonfinite values are not silently changed to null.

The current controls show connection/run state and load committed results through
the SP3 viewer. LiveSession exists but full scene wiring, forms, layout editing,
view history and pinned presentation exports remain the following plan tasks.
The `trace_available` descriptor currently provides attempt/generation/length/hash
and URL; the design's additional last-cursor hint remains to be integrated with
the live metadata path. It must not trigger repeated full-prefix scans.

## Verification

| Check | Evidence |
|---|---|
| macOS CPython 3.14.1 full reviewed suite | 2,965 passed, one native-Windows skip, 99.07% branch coverage, 400.88 s |
| Linux CPython 3.14.0 Studio suite | 121 passed, one packaging test skipped because Node was absent from that command PATH, 157.81 s; final affected app/framing 21 passed (44.76 s), blocked-send/framing 20 passed (0.32 s) |
| macOS CPython 3.11.10 wheel-installed transport/app suite | 61 passed (58.59 s); final affected app/framing 21 passed (33.83 s), blocked-send/framing 20 passed (0.23 s) |
| Installed-wheel Studio smoke | Chromium and WebKit: bootstrap, automatic execution, committed result, authenticated reload and manual second execution; zero page errors |
| Linux Firefox Studio smoke | Same flow against the isolated source installation; zero page errors |
| TypeScript workspace | 367 tests and type checks passed; production build passed |
| Recorded-viewer regression | 20 Chromium/WebKit tests passed on macOS (50.6 s) |
| Static checks | Ruff, formatting, repository-wide ty and diff whitespace checks passed |

The initial full run had 2,932 passes, one reproduction failure and 98.25% coverage.
The reproduction failure correctly rejected a runtime changed by concurrent asset
packaging during verification. Its isolated rerun passed. Subsequent full runs
freeze **all** installed source/runtime files, including packaged assets. Additional
failure tests restored the existing 99% gate; no threshold or exclusion changed.

The first CLI/browser smoke fixture used the wrong non-Git include section and
was corrected to `tool.simulatte.bundle`. Other fixture mistakes (indentation,
missing placement kind and an incorrect store assertion) were corrected without
changing product behavior. Final frontend-only assets were refreshed after the
Python gate, then verified in fresh installed-wheel smoke tests.

## Dependencies and remaining acceptance

Locked Starlette 1.7.0, Uvicorn 0.54.0, watchfiles 1.3.0 and websockets 17.2 were used.
Only websockets was added to the optional extra. D88 records the explicit backend
choice and its deprecation warning. The existing Starlette/httpx test warning
remains; unrelated dependency upgrades were avoided.

Outstanding: full editor/live integration, native Windows lifecycle/Studio checks,
actual Safari manual checks, broader failure/performance acceptance and user docs.
WebKit automation is not actual Safari evidence. Experiments, statistical
comparison and SSH execution workers remain SP5.
