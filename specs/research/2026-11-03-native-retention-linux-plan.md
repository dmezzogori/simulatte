# Integrated bounded-retention Linux validation

Davide authorized the proposed Linux gate on 2026-11-03. Work inline from signed
`dfbb1a1e21eb885013f44ada987c8172e469aef1` in the isolated native research worktree.
Preserve native v3 research sequencing after v2/Studio and defer release baselines.

- Use a new isolated checkout on strangelove, transferred by Git bundle. Preserve
  earlier experiments and machine configuration. Use the existing managed CPython
  3.14.0 and GCC 15.2. Build Linux images from the canonical integrated sources;
  reuse only the matching pure-Python core wheel, never macOS native images.
- Keep production/core/provider/workload and existing gate sources unchanged.
  New Linux orchestration, evidence and report live separately. Verify frozen
  source, archive, ABI/ELF and evidence hashes and preserve historical raw files.
- Run existing regression, lint/type/lock and clean-install rejection checks.
  Instrument canonical engine/stock/retention with standalone ASan/UBSan and leak
  detection; exercise allocation failure, compaction, handles and overflow. Fully
  instrument engine plus CPython binding in a private copied installation; run
  existing native API tests with Python leak detection disabled.
- Run the same serial 25-case installed Coordinator matrix: three paired seeds,
  16k/131k/1m jobs, lazy 64-job windows, semantic digest/KPI trace path, overload,
  observers, repeated parent and source-deleted fresh reproductions. Same fixed
  acceptance limits; do not run compilers/tests concurrently or silently relax
  a failing memory criterion. Collect parent/child/tree RSS and store/trace bytes.
- Reuse the existing gate audit, independently check shared macOS/Linux physical
  KPIs/events/input hashes, and report native state matches separately from whole
  platform-specific fingerprints. Do not compare host speeds as an engine gain.
- Save reproducible evidence, signed local commit and Cortex checkpoint. No push,
  publication, deployment target, extra ABI, remote native slot or actor expansion.
