# SP4 incremental live-reader foundation

Status: Task 9 implementation; Studio server/UI integration and release acceptance remain outstanding.

`LiveRecording` checks attempt/generation/length identities, incrementally verifies SHA-256 over new bytes, parses metadata from the previous committed offset and atomically exposes a new `RecordingView`. Extensions reject cut records, uncommitted chunks, inconsistent footer/index/trailer data and corrupt hashes. A coalesced notification can advance by several generations because its entire contiguous new extent is checked. Each old extent retains its immutable source.

Static and live readers share trace parsing, recording queries and playback. Retained generations share the existing four-chunk decoded cache and two-read scheduling; candidates use distinct new chunk identities to prevent failed validation from poisoning a later candidate. The existing oversized-single-chunk diagnostic remains; cache estimates do not represent whole-browser RSS. Catalog, observer, scalar and KPI metadata belong to immutable generation views.

The `LiveSession` adapter keeps one newest pending prefix, supports explicit Follow and manual playback, and fences pending frames on reset/invalidation/disposal. Seek and step disable Follow. Coordinator terminal status is separate from the trace footer. This adapter is not yet connected to the Studio websocket or editor.

## Verification

Workspace type checking, **357 TypeScript tests**, and frontend/player builds pass. Tests cover append boundaries, observer-only and KPI-only updates, equivalence with static queries, no reads below the previous prefix during extension, old-view stability, bad hashes/attempts/generations, partial records/uncommitted chunks, shared cache limits, candidate cache isolation, two-read I/O, queued-read cancellation, terminal-trailer cancellation, coalescing, manual seek during Follow, stale reads after attempt/revision changes, and failure/recovery.

The existing standalone reader bundle test initially failed because SSR externalized the new hash dependency. The fixture now bundles that dependency explicitly and retains its runtime filesystem/network prohibitions. All 20 Chromium/WebKit viewer/export browser tests passed on macOS. Firefox could not launch its automation profile on macOS (the same failure recorded in SP3), including an isolated `/tmp` launch. All 10 Firefox viewer/export tests passed on native Linux in 39.2 s, using the isolated source archive SHA-256 `b88d9ee02b04a227c397c122891ed04df1dd889035c75e18574868c510a10a0d`. That copy predates only the adapter disposal guard and its unit test; the viewer/export sources exercised by the browser suite match. The current adapter is not yet wired into those browser flows. Fresh packaged assets also pass all 19 existing Python viewer-server tests (one existing Starlette warning). No live browser/server flow or actual Safari verification is claimed here.

The hashing dependency is pinned to `@noble/hashes` 2.0.1; see D87 and its primary documentation link. Python production files are unchanged by this reader work. The preceding Python gate at commit `0ee871d` passed 2,897 tests with 99.03% branch coverage, one native Windows skip and one existing warning.
