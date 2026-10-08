# Simulatte Studio: decision log

Decisions for the work described in [`2026-10-08-studio-global-design.md`](2026-10-08-studio-global-design.md). Each entry records what was decided, why, and what was rejected. A later session must not reopen a decision without new evidence; when one is reversed, mark it superseded and add a new entry.

Format: **D<n>. Title** (date, source). Decision. Rationale. Rejected alternatives.

## Brainstorming, 2026-10-08 (Davide with Claude)

**D1. Priority of uses.** Client-facing animation first, visual debugging second, statistics third. All three are in 1.0 except interactive live debugging. Rejected: debugging-first (live connection) as the v1 driver; a trace viewer with seek covers most debugging needs.

**D2. Record and replay as the v1 core.** Runs produce a trace that the viewer replays; streaming during a run is supported but not controlling a running simulation. Rejected: live-control architecture first.

**D3. 2D top-down renderer, 3D-ready data.** Rejected for 1.0: 2.5D isometric (sprite art cost), full 3D (asset, camera and performance cost; FlexSim's strongest ground).

**D4. Continuous coordinates with a snapping grid, not a grid world.** The grid (single spacing `dx, dy` and an origin) is used for snapping and path network generation; entities keep true footprints. Rejected: entities on grid intersections as the core data model (inflexible shapes, unrealistic plants).

**D5. Three layout sources in 1.0** (auto, code, studio file) with per-property precedence auto < code < file. Davide's call: all three in v1.

**D6. Path network from generators plus overrides.** Lattice (blacklist) and lanes (whitelist) generators, explicit graphs still supported; arcs have enabled, direction and speed limit. Rejected: lattice only (unrealistic for industrial AGV layouts); lanes only (slow for prototypes and grid-based research).

**D7. The studio edits physical layout in 1.0** (paths, blocked areas), and edits trigger re-runs. Davide's call ("aim big"). Rejected: presentation-only editor in v1. Consequence: `layout.json` is a model input; physical and presentation hashes are separated.

**D8. Local studio server.** `simulatte studio model.py` serves the UI from a local Python process; runs happen in worker subprocesses. Rejected: static viewer with manual re-runs (clumsy loop, dead end for live debugging); Jupyter widget as primary (awkward for demos and full-screen editing); desktop app (packaging cost).

**D9. Five sub-projects** in order: events and trace, layout, viewer, studio, experiments. Each with spec, plan, two adversarial reviews by Codex (Astra), corrections, then Davide's go-ahead or a third review.

**D10. Event bus unifies logging.** `SimLogger` becomes bus subscribers; free-text logging becomes `log` events. Rejected: keeping `SimLogger` alongside a separate bus (two overlapping mechanisms).

**D11. Explicit emits from components, observer-only subscribers.** Rejected: deriving events from existing hooks and logs (incomplete); wrapping SimPy internals (fragile).

**D12. Stable entity ids with optional names.** Placeable entities take an optional `name`; otherwise per-kind construction-order ids. Transient entities get sequential ids, replacing `uuid4`. Orphaned layout entries are reported, not applied. Rejected: mandatory names (breaks every model; heavy for research scripts).

**D13. PixiJS for the scene**, as a framework-independent TypeScript module. Rejected: Canvas2D and SVG (do not scale to thousands of moving entities).

**D14. React 19 with React Compiler for the UI shell**, with clock-bound panels kept outside React's render cycle. Based on a researched comparison (2026-10-08): React scored highest on editor components (dockview, react-arborist, cmdk, Radix, Base UI), longevity and agent friendliness. Rejected: Svelte 5 (no maintained docking layout or tree view; would be reconsidered if docking were reduced to split panes); SolidJS (2.0 still a release candidate with breaking changes; thin component ecosystem).

**D15. Studio controls layout, seed, parameters and experiments in 1.0.** Davide's call ("aim big"). Typed `Params` dataclass generates forms. Rejected: layout and seed only; parameters without experiments.

**D16. KPIs computed only in Python**, as bus collectors whose output is stored in the trace. Rejected: computing KPIs in the viewer (two sources of truth).

**D17. Replications record KPIs only by default; full traces on demand** by deterministic re-run from the stored seed, with fingerprint comparison.

**D18. Local and remote SSH workers in 1.0.** Davide's call ("aim big"). One stdio protocol for both transports, plain `ssh` with the user's configuration, generic over any network. Rejected for 1.0: local pool only.

**D19. Remote environments shipped automatically with uv.** The model must be a uv project for remote runs; source bundles by content hash, `uv sync --frozen` on the worker. Rejected: pre-provisioned environments (fragile); Docker images (slow loop, Docker required everywhere).

**D20. uv installed on remote hosts only through an explicit `simulatte workers setup <host>`.** Rejected: silent auto-bootstrap (installing software unasked); documentation only (no connection check).

**D21. Release strategy: sub-projects merge into `main` and ship as 0.13–0.17, then 1.0 as stabilization.** Rejected: a long-lived feature branch merged at the end (months of drift and rebases, nothing ships); a mixed strategy.

**D22. Design documents live in root `specs/` and `plans/`**, which the sdist already excludes, not in `docs/` (the published website).

## Added while writing the spec (open to objection)

**D23. Per-environment named RNG streams replace the global `random` module.** Found while writing the spec: `Runner` seeds the global generator and `distributions.py` and `router.py` draw from it. Named streams make runs reproducible inside a long-lived process and enable common random numbers across experiment configurations.

**D24. Studio binds to 127.0.0.1 with a session token**, because it executes user code.

**D25. Zero-subscriber overhead budget of 3 %**, enforced by a CI benchmark on CPython and PyPy.

**D26. Layout lifecycle: physical properties freeze at the first physical access** (for example `layout.graph()`), presentation properties after `build` returns; auto-layout is presentation-only.
