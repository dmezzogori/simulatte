# Simulatte Simplification Plan (excludes `src/simulatte/jobshop`)

## Goals
- Make the core simulation pieces explicit and composable (no hidden singletons).
- Remove layers that add little behaviour (Protocol soup, observer wrappers, over-nested requests).
- Keep only SimPy extensions that deliver unique value; lean on stock SimPy otherwise.
- Separate core logic from optional reporting/plotting.

## Phases
1) **Environment explicitness**
   - Remove `Singleton` + `EnvMixin` reliance; constructors take `env: simpy.Environment`.
   - `Simulation` owns/creates env and passes it down; allow injecting a custom env for tests.
   - Stop global ID resets coupled to env creation.
   - Status: Default env fallback removed; `EnvMixin` now requires an explicit env; as_process raises if env missing; all core components, requests, observables, SimPy helpers, and tests (excluding jobshop) pass env explicitly.

2) **Lean data model**
   - Flatten requests to `PalletOrder` with `OrderLine`s (product, cases); drop prev/next links.
   - Keep only structural typing that is truly needed; replace protocol imports with plain types.

3) **Slim SimPy wrappers**
   - Replace `MultiStore`/`FilterMultiStore`/`Sequential*`/`HashStore`/`queue.py` with std `Store` + helpers.
   - Keep a minimal helper if simultaneous put/get is essential; otherwise delete.

4) **Control layer cleanup**
   - Merge policy modules into small strategy functions inside controllers.
   - Use `abc.ABC` for abstract controllers; remove unused hooks.

5) **Observability/reporting split**
   - Drop `ObservableArea`/`Observer` indirection; use direct events or queues inside picking cell.
   - Move plotting/tabulate into optional `reporting` module; core returns data only.

6) **Testing and follow‑ups**
   - Update/adapt tests (excluding jobshop) after each phase.
   - Add a minimal end-to-end smoke covering env injection + feeding flow.

## Done / In‑flight
- [x] Write high-level plan (this file)
- [x] Phase 1: environment explicitness (explicit env required end-to-end; tests updated; jobshop left untouched)
- [x] Phase 2: lean data model (requests collapsed to OrderLine/PalletOrder aliases; protocols now re-export concretes; picking cell no longer chains prev/next)
- [x] Phase 3: slim SimPy wrappers (custom stores replaced with lightweight list-backed helpers; sequential/multi/filter/hash stores simplified)
- [ ] Phase 4: control layer cleanup
- [ ] Phase 5: observability/reporting split
- [ ] Phase 6: tests aligned
