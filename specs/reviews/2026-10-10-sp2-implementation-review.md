# SP2 implementation review

The initial whole-branch review below found one Important and three Minor issues.
All four were fixed in `addf0fa1729077fa618305efcde496c116ae55f8`; the scoped re-review
at the end approved the changes with no remaining findings.

The accepted design amendments A1–A5 remain unchanged. Acceptance evidence is in
[the SP2 report](../research/2026-10-10-sp2-acceptance.md).

# Final independent SP2 branch review

Reviewed base `b1467709e88cf640155b83c037f06573fcddd875` through HEAD
`6af9de82ffad019d8bace4559c0cb48c64e8664c` against the approved revision-3 design,
implementation plan, global constraints and current rulings/task-review ledger.
Read-only product review; this report is the sole owned write. No subagents,
commits, suite reruns, benchmark reruns or source/test changes.

## Strengths

- The stage coordinator has a coherent ownership/freeze boundary, authoritative
  compiler target view and latched failure path. Saved explicit stage prefixes
  survive separately from presentation auto fill (`lifecycle.py:187`,
  `dependencies.py:238`). The earlier pending-target and D30 corrections are
  present, including shared fresh/replay eligibility validation.
- Geometry uses exact predicates on materialized coordinates, preserves source
  segment identity, and separates disabled definitions from enabled canonical
  runtime objects. Connector owner exemption, approach aliases and ordered
  property overrides are implemented at their appropriate boundaries
  (`geometry.py:102`, `connectors.py:89`, `overrides.py:53`).
- Built-in binding preflights before publication, installs nodes once, preserves
  AGV attachment order, seals static endpoints and checks canonical movement
  identity before state changes. The unchanged explicit path is separately
  exercised (`binding.py:72`, `src/simulatte/intralogistics/agv.py:168`,
  `tests/layout/test_binding.py`).
- Trace 1.1 metadata is finalized after binding, bounded independently of queue
  backpressure and exposed immutably. Known-schema nested validation now shares
  the canonical schema in TS; old 1.0 and opaque future layout cases have focused
  coverage (`src/simulatte/trace/writer.py:414`, `src/simulatte/trace/reader.py:677`,
  `studio/packages/trace/src/layout-schema.ts:4`).
- Acceptance evidence is unusually thorough and clearly bounded: local runtime
  comparisons, installed core-only artifacts, executed docs and unchanged
  benchmark gates are distinguished from unexecuted CI lanes and #59. I relied
  on Task 10's independent evidence audit rather than duplicating those runs.

Paths above and below are relative to `src/simulatte/layout/` unless fully
qualified or explicitly identified otherwise.

## Issues

### Critical

None found.

### Important

#### I1 — Model-visible physical values disagree with the equivalence used by physical identity and saved reuse

**Locations:** `src/simulatte/layout/dependencies.py:155` (especially the raw
return at line 170 and defaults at 159/173), `layers.py:319`, `types.py:40`,
`identity.py:39`, `identity.py:60`, `dependencies.py:238`.

Physical reads return authored numeric values and insertion-ordered mappings.
Identity/reuse normalize rotation modulo 360, signed zero and float64 geometry,
and compare/hash maps without authored key order. Consequently an edit considered
physically equivalent can change the actual value or ordinary iteration order a
stage/binder consumes. This is a false reuse decision, not merely a different
presentation of equal data.

**Confirmed numeric reproduction:** a stage reads `hall.rotation`, creates an
extra server when the value is >=180, and is otherwise unchanged. The original
file has 360; the replacement file has 0. Original preparation reads 360 and
creates `extra`; fresh preparation reads 0 and omits it. Saved replay returns
`requires_rerun=False` and retains `extra`:

```python
from simulatte import Environment, Layout
from simulatte.server import Server
from simulatte.layout import reresolve_presentation


def make(rotation):
    env = Environment()
    patch = {"schema_version": "1.0", "placements": {"hall": {"kind": "server", "rotation": rotation}}}
    layout = Layout.from_dict(env, patch)
    Server(env=env, capacity=1, name="hall")
    angle = layout.ref("placements", "hall", "rotation")
    observed = []

    @layout.stage("machines", reads=[angle])
    def machines(ctx):
        value = ctx.physical(angle)
        observed.append(value)
        if value >= 180:
            Server(env=env, capacity=1, name="extra")

    return layout.prepare().bundle, observed


original, read_a = make(360)
fresh, read_b = make(0)
edit = reresolve_presentation(
    original, {"schema_version": "1.0", "placements": {"hall": {"kind": "server", "rotation": 0}}}
)
print(read_a, read_b, edit.requires_rerun)
print(list(original["targets"]), list(fresh["targets"]), list(edit.bundle["targets"]))
```

Observed: `[360] [0] False`; `['extra', 'hall'] ['hall'] ['extra', 'hall']`.

Two additional focused cases establish the representation boundary:

| Physical read | Original / fresh observation | Equal physical hashes | Replay requires rerun |
|---|---|---|---|
| `position`, applying `math.copysign(1, value[0])` to `[-0.0,0,0]` / `[0.0,0,0]` | `-1.0` / `1.0` | yes | false |
| `ports`, applying `list(value)` to the same `z`/`a` records supplied in opposite map order | `['z','a']` / `['a','z']` | yes | false |

The map case uses two purely visual ports, each
`{"position":[0,0,0],"approach":[1,0,0]}`, on a named server. A normal stage
iterating that map can schedule processes or assign generated entities in a
different order. No private mutation, random callback sidechannel or unsupported
presentation read is involved. The existing normalization test at
`tests/layout/test_presentation.py:94` checks hashes/reuse but does not assert
what its binder actually receives, so it misses this disagreement.

**Fix:** establish one canonical immutable physical-value representation before
model consumption, and use that same representation for observations/comparison.
Normalize schema geometry to float64, rotation and signed zero as already
specified; provide canonical map iteration recursively while preserving authored
list order. Cover direct leaf, parent/container and default-container reads,
stage and binder access, and finalized covered reads. Preserve source eligibility,
tombstones, frozen prefixes and author-layer intent. Add a stage-output/replay
regression, not only hash equality assertions.

**Contract versus implementation:** numeric normalization is already required by
design §§4/8.3; code must change. The design describes map canonicalization for
identity but does not explicitly spell the iteration order of `physical()` maps.
Root should record the narrow ruling that model-facing physical maps have the
same canonical key order used by identity (including nested/default containers).
Changing map-order identity instead would change the approved canonical-map
contract and is not the recommended repair. Ordinary list order remains physical.

### Minor

#### M1 — Valid float64 geometry written as a large JSON integer fails only at bundle freezing

**Locations:** `src/simulatte/layout/layers.py:319`,
`src/simulatte/layout/bundle.py:315`; wire boundary
`src/simulatte/_wire.py:120`.

The parser accepts a finite geometry number such as `10000000000000000`, but
author/resolved layers retain it as an arbitrary Python integer. Final bundle
freezing then raises `OverflowError: integer outside +/-(2**53 - 1)`. The same
coordinate written `1e16` succeeds. Confirmed with a named server placed at
`[coordinate,0,0]`, no network or physical callback, and `layout.prepare()`.
The earlier physical-read probe with width `9007199254740993` reached the same
failure; it therefore did not establish a successful large-integer false-reuse
case and is not claimed as one.

This is limited to extreme numeric inputs, so it is not separately release
blocking, but the schema promises float64 geometry rather than wire-safe integer
geometry. Coordinate input spelling should not decide whether preparation works.
Repair the geometry representation at the schema-aware data/wire boundary,
including stored author layers. Preserve meaningful author distinctions such as
unsets, list order and source; do not solve this by silently restricting geometry
to safe integers or weakening the core wire integer contract. Add integer/float
equivalence through prepare, bundle round trip and trace encoding.

#### M2 — An unplaced AGV acquires an authored world position in the resolved bundle

**Locations:** `src/simulatte/layout/auto.py:261`,
`src/simulatte/layout/bundle.py:449`.

For an AGV with `initial_node=None`, `layout.place(cart, position=Point(30,40))`
leaves `cart.current_node is None` and no derived position, but
`bundle['resolved']['placements']['cart']['position']` is `(30.0,40.0,0.0)`.
Replacing that file position through saved replay changes it to `(60,70,0)` and
returns no rerun. Confirmed using the existing `vehicle` test helper with a
named `cart` in `Layout(Environment())`.

Design §4 says an unplaced AGV has no world position; its authoritative location
comes from node/motion. Bound AGVs are handled correctly because `_effective`
overwrites authored coordinates with a derived position, but absence of a
derived position leaves a misleading fallback. This is a metadata/display
correctness issue, not a runtime AGV relocation.

Preserve the authored entry in layers, but omit an effective world position when
an AGV has no bound node. Apply the same rule during initial finalization and
saved presentation replay. Test both unplaced and bound cases. No new contract
decision is needed.

#### M3 — Final diagnostics use lexical stage names rather than stage execution order

**Locations:** `src/simulatte/layout/layout.py:416`,
`src/simulatte/layout/bundle.py:699`.

Final sorting discards the layer/network ordering and compares `stage or ''`
lexically. Confirmed with stage `zeta` declaring orphan `ghost-z`, followed by
stage `alpha` with `after=['zeta']` declaring `ghost-a`: diagnostics return
`alpha/ghost-a` before `zeta/ghost-z`. Design §11 requires stage order, then path,
code and target. Ordering is still deterministic; this is a minor diagnostic
contract discrepancy, not simulation corruption.

Use the saved execution/declaration stage order consistently for preparation and
replay diagnostics, with an explicit position for file/unstaged diagnostics.
Include non-lexically named stages and a dependency-reordered registration case.

## Declined to judge

- SP3 rendering/viewer behavior and SP4 editor/source-snapshot/revision storage:
  explicitly separate projects; reviewed SP2's stored data and reuse API instead.
- Segment reservations, head-on collision exclusion and lane-lock semantics:
  approved A1 deliberately retains node-only traffic. No absence finding.
- Requiring a removed parent-lane override to transfer to new children: approved
  A2 requires staleness. Disabled definitions retained outside the runtime graph
  are likewise an explicit ruling, not a missing-node defect.
- Relaxing manual-step activation, resource-owner sharing or canonical AGV
  identity rules: approved A4/A5 intentionally require these guards.
- New performance budgets, an unrelated #59 fix, release/version changes or
  global packaging/branding work: excluded by the approved scope. Acceptance is
  assessed against existing gates and stated local measurements.
- Extending inherited runtime planner arithmetic to all extreme finite-coordinate
  path-cost sums: considered as a numeric edge risk but not expanded into a
  planner redesign. No regression was established; the reviewed SP2 guarantees
  concern finite materialized geometry and exact topology predicates. Root may
  separately choose a finite route-cost envelope; this report does not assert it.

## Assessment

**Ready to merge: with fixes.** I1 permits reuse of simulation results for a model
whose stage-visible inputs produce different behavior, so it should be corrected
before integration. The three minor items are bounded and can be adjudicated in
the same coherent fix wave; they are not independently elevated to blockers.

The overall architecture and task decomposition are sound, and the reported
acceptance is strong. Root's planned documentation-only recording of the actual
99.36615% combined coverage/signature result and final review is intentional
bookkeeping, not an implementation gap.

## Review and verification bounds

- Reviewed the whole change by coherent passes over the canonical requirements,
  current rulings, layout types/schema/layers, lifecycle/dependencies, geometry and
  networks, runtime integration, identities/replay, Python/TS trace changes,
  relevant test assertions, docs and acceptance interpretation. Inspected changed
  implementation directly and integration diffs; did not manually rederive every
  repetitive fixture or raw measurement in the 330-file package.
- Read Task 9's original and scoped re-review and Task 10's independent evidence
  audit. Existing full-hook/coverage, PyPy, TS, packaged-artifact, Pyodide,
  fresh-process and benchmark results were not rerun or claimed as new review
  executions.
- Executed only the focused snippets above for concrete unanswered doubts. The
  combined first numeric probe stopped at the disclosed safe-integer wire error;
  signed-zero/map cases were then run separately. No test-suite or benchmark
  repetition occurred.
- Named existing-code inspections outside new layout modules: entity registry
  attachment/name publication, environment activation/scheduling/manifest flow,
  AGV and fleet relocation, graph route-distance implementation and wire freezing.
  These were integration-boundary checks, not an unrelated refactor audit.
- This is a source and focused behavioral review, not a formal proof of all
  geometric, hostile-metadata or large-resource cases. Final product working tree
  remained clean before this ignored scratch report was written.


---

# Final SP2 scoped re-review

Reviewed `6af9de82ffad019d8bace4559c0cb48c64e8664c` →
`addf0fa1729077fa618305efcde496c116ae55f8` against the original final-review
I1/M1/M2/M3 and root's recorded canonical-representation ruling. Scope is those
repairs and breakage introduced by this fix, not a second broad branch audit.

## Strengths

- One normalization implementation now supplies model-facing physical values and
  query observations. Its separate author-materialization mode retains raw angles,
  signed zero and list intent while avoiding the unsafe-integer wire mismatch.
- The regressions assert actual callback observations and stage-created targets,
  including parent versus leaf reads, binder/stage/finalized access and escaped
  Unicode map-key order. They do not merely repeat hash assertions.
- Effective AGV presentation and diagnostic ordering use shared fresh/replay
  paths, so the fixes do not leave parallel implementations with different rules.

## Original findings

| Finding | Disposition | Evidence |
|---|---|---|
| I1 — physical values disagree with identity/reuse | **ADDRESSED** | `src/simulatte/layout/dependencies.py:98` normalizes observed values; `:159` and `:170` normalize immutable default and present read results. `identity.py:39` applies float64 geometry, modulo rotation, positive zero and recursive canonical escaped-key ordering while preserving list order. `tests/layout/test_final_regressions.py:20` checks the original behavioral risk across stage/binder and parent/leaf access, compares fresh/replay target sets, and checks covered finalized reads. `:76` covers default containers. The spec/guide explicitly record root's map-iteration ruling. |
| M1 — valid large geometry integers fail wire freezing | **ADDRESSED** | `src/simulatte/layout/layers.py:36` and `:68` materialize schema geometry in file/code/auto layers. `bundle.py:313` materializes known bundle data and path-addressed query observations/defaults before wire freezing. The canonical=False mode retains authored angles/signed zero; counts and logical indices remain integers. `tests/layout/test_final_regressions.py:107` checks code/file inputs at 1e16 and 1e308 through prepare, bundle round trip and trace encoding. `:186` covers path-addressed values/defaults; `:208` protects opaque future data. |
| M2 — unplaced AGV has authored effective world position | **ADDRESSED** | `src/simulatte/layout/bundle.py:452` removes authored AGV positions from the effective projection before adding only derived positions. Both initial finalization and replay already call this helper. `tests/layout/test_final_regressions.py:139` checks bound/unbound controls, fresh/replay output and preservation of author-layer coordinates. |
| M3 — diagnostics sorted by lexical stage name | **ADDRESSED** | `src/simulatte/layout/errors.py:39` orders by first declaration in saved execution order; `layers.py:291`, `layout.py:416` and `bundle.py:705` share it. `tests/layout/test_final_regressions.py:161` covers stage zero, zeta-before-alpha, file diagnostics and reverse registration with explicit dependencies, for preparation and replay. |

## New issues introduced by this fix

- Critical: none identified.
- Important: none identified.
- Minor: none identified.

The focused outside-diff inspection of `src/simulatte/_wire.py:304` and `:331`
confirmed that the chosen escaped-key sorting matches the codec's canonical order.
Existing eligibility checks, tombstone states and saved-prefix/target handling
remain in their prior paths. The fix does not add callbacks or network rebuilding
to replay or modify simulation runtime hot paths.

## Declined to judge

None within this scoped fix. No newly discovered behavior was dismissed as outside
the requirements. The original broad review's explicit boundaries and root's
adjudications remain unchanged; fresh post-fix acceptance measurement is a separate
ongoing task, not an omitted implementation repair in this re-review.

## Assessment

**Approved: I1, M1, M2 and M3 addressed; zero findings remain from this review.**
The concrete producer/consumer mismatch is corrected and the regressions verify
the original model-behavior trigger. Ready for root's acceptance-evidence refresh
and integration decision; this approval does not claim those concurrent tasks
have completed.

## Verification and bounds

- Read the fix implementation, spec/guide clarification and all 17 new regression
  cases in one coherent scoped pass, plus the fix report. Generated layout fixture
  pairs were assessed through the recorded Python/TS complete-data conformance
  checks rather than manually rederiving their repetitive contents. Only the two
  layout fixture pairs changed; old 1.0 fixtures remain untouched.
- Read `/tmp/sp2-final-focused-green.txt`: 17 passed; `/tmp/sp2-final-focused.txt`:
  714 passed; `/tmp/sp2-final-wire-green.txt`: 160 passed after the final wire fix;
  `/tmp/sp2-final-ts-test.txt`: 261 passed. Read `/tmp/sp2-final-commit.txt`:
  unchanged Ruff, format, ty and full pytest hooks all passed. The reported
  99.36691052% combined coverage and signature are root/implementer evidence, not
  a new coverage/signature audit by this reviewer. TS typecheck success is recorded
  in the fix report; an attempted guessed `sp2-final-ts-typecheck.txt` path did not
  exist, so this review does not claim to have independently read that log.
- No suite, benchmark or reproduction was rerun: the new assertions directly
  address the original reproductions, and code inspection raised no concrete
  unanswered doubt requiring another run.
- No product, test, docs, index, HEAD or branch edits. No subagents. This owned
  scratch report is the only write. This remains a source/focused-evidence review,
  not a formal proof of all possible inputs.
