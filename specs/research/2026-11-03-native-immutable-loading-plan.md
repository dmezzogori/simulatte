# Verified immutable native-binary loading experiment

Davide explicitly authorized the proposed experiment. Compare the current
per-attempt binary paths with a fixture-local immutable, content-addressed copy
of the same extension/engine pair. Retain fresh children, source/runtime evidence,
per-run content checks, normal state/KPI/artifact/reproduction validation and
publication. No production cache, installed backend, package/release, remote work,
actor expansion, native transition change or push is included.

1. Add a research asset-store/loader helper. Address a pair by its exact binary
   hashes, names and interpreter ABI; publish atomically and read-only. Check the
   snapshotted pair and the selected pair before importing. Reject missing,
   corrupted, writable, symlinked or mismatched shared entries without repair or
   fallback. Recheck identities/content after loading and verify actual extension
   and linked-engine locations. This detects ordinary mutation, not malicious
   same-user native-code attacks.
2. Use checked disposable runtime/project instrumentation, retaining identical
   source/runtime bytes across path choices. Pass selection through private
   research child configuration. Keep loader diagnostics outside semantic outputs.
   Leave repository production/native C sources and previous evidence unchanged.
3. Test identity, publication/reuse, corruption/missing/read-only/symlink checks,
   and mutation during import. Run managed pairs, original-loader controls,
   reproduction, ready cancellation and invalid-cache failure with no publication.
4. Measure both whole workflow and loader/verification intervals using the existing
   independent/feedback, 5k/20k, three-replication workloads. Include active/dormant
   phase controls and three randomized repeats. Preserve raw diagnostics, file
   hashes, process identities and first-use information. Report one-time cache
   construction separately; do not preload native images outside timing.
5. Audit results, accounting, unchanged source/assets and evidence. Adopt no
   performance claim without a consistent whole-workflow benefit. Report a negative
   result if the hypothesis fails. Commit locally and save a Cortex fallback while
   the service is unavailable.
