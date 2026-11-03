# Declared native assets in managed execution

Davide authorized the recommended integration step. Add explicit model native
bundle declarations, pin their captured file hashes during preparation, and give
the registered backend verified bundle handles through BackendContext. The
coordinator/run store owns immutable publication, invalidation and manual pruning.
Keep fresh children, actual import provenance, byte checks and reproduction.

- Declarations are bounded, data-only, normalized project-relative native binary
  members, separate from data inputs. Existing empty declarations preserve metadata.
  Hash/ABI manifests are captured documents tied to the request's source snapshot.
- Local execution materializes captured source, acquires read-only content-addressed
  bundles and holds leases until the child and cleanup finish. Slot threads share
  a store owner/lock; existing RunStore process fencing excludes other writers.
  Atomic publication fsyncs payloads and directory entries. Invalid entries fail
  without automatic repair. Missing/explicitly pruned entries rebuild from pinned
  snapshots. Pruning removes only idle derived bundles and abandoned staging;
  authoritative request/source records retain their current retention policy.
- Before model import, children verify manifests/source/cache/ABI and allow exactly
  declared native files through ordinary origin auditing. BackendContext exposes
  named verified paths and checked extension loading. Recheck before publication.
  No environment-variable selection or research Coordinator override is required.
- Initial support is local POSIX execution. Unsupported remote/Windows native-asset
  execution fails explicitly; ordinary models remain unchanged. No package/release,
  actor expansion, C transition changes, full native tracing or push.
- Verify declaration/manifest validation, immutable reuse/invalidation, concurrent
  slots, failed publication and interrupted staging, mutation rejection, pruning
  leases, readiness cancellation and reproduction after cache eviction/source
  relocation. Run relevant existing regressions, docs/types/lint and real paired
  research experiments. Measure the integrated path using native state/KPI/artifact
  checks; preserve all prior evidence and record a local commit/Cortex checkpoint.
