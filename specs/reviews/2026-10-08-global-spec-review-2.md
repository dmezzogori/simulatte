# Global spec review 2 (Astra, Codex GPT-6-Astra xhigh)

- **Date:** 2026-10-08
- **Reviewed commit:** 18d1dfd
- **Verbatim findings below; triage at the end.**

B1 — blocker — The semantic digest still depends on observer configuration.
Sections: C1.2, C1.3, C1.6, §8. A3/A5/A34 remain partial.
Hashing all non-log events includes their global seq and optional observer-generated KPI events.
Failure: enabling one preceding log changes a domain event from seq=1 to seq=2; enabling a
collector adds hashed events. Identical physics then produces different fingerprints. Initial state
also needs explicit coverage: equal deltas do not prove equal trajectories from different starting
states. Fix: define a semantic projection with domain events, an observer-independent ordinal,
canonical semantic fields, and complete initial/post-bind state. Exclude diagnostics, derived
samples, and presentation fields. Distinguish this logical comparison from comparison of complete
trace artifacts.

B2 — blocker — Physical dependencies are discovered after their hash is frozen.
Sections: C2.6–C2.7. A2 remains partial.
Resolve computes hashes and freezes the layout; the following bind phase discovers model-read
physical dependencies. Failure: binding reads a previously presentation-only width or label to
configure processing. Its value affects results but was absent from the finalized physical hash.
Fix: freeze resolved values first, perform binding and dependency discovery, then finalize the
physical dependency closure and hash before execution. Reject newly introduced dependencies after
that point.

B3 — major — The new lifecycle still needs declaration-aware validation and an activation boundary.
Sections: C1.1, C2.7, C3. A1/A19 remain partial.
Layout nodes become entities at binding, yet resolve assumes all entities exist and checks orphans
before binding. Handles also do not make synchronous setup operations safe. Failure: a valid node
override is rejected before its node binding exists; an initial fleet.submit() attempts routing
through unresolved handles. The existing simple example (examples/intralogistics_simple.py:22)
submits orders before running, and submit() (src/simulatte/intralogistics/fleet.py:190) immediately
performs dispatch selection. Fix: validate against declared definitions as well as attached
entities, specify a user-accessible bind/activation phase, and define whether pre-bind physical
operations queue or raise. Define how constructor inputs derived from layout values are supplied.

B4 — major — Every drawable footprint becoming physical contradicts presentation-only auto-layout.
Sections: C2.3, C2.4, C2.6. New.
C2.6 makes the position and footprint of every entity with a footprint physical when generating a
network. C2.4 forbids auto-layout from influencing physics. Failure: a mixed production/AGV model
leaves servers auto-positioned but gives them drawable footprints. Including those footprints makes
auto-layout physical; excluding them violates the unrestricted rule. A moving AGV’s footprint also
must not become a permanent obstacle. Fix: separate visual footprints from participation as static
obstacles. Apply transitive dependencies to participating obstacles, whose physical positions must
be explicitly available.

B5 — major — A run spec contains values that are unavailable when it must be submitted.
Sections: C1.6, C2.7, C4, C5.1–C5.2. A14/A15 remain partial.
The immutable run spec includes resolved layout hashes and manifest inputs, but layout resolution
requires executing build and binding. The execution runtime may also differ from the bootstrap
supervisor’s runtime. Failure: the coordinator must either execute model code prematurely, invent
incomplete hashes, or mutate the supposedly immutable spec after submission. A supervisor’s hello
cannot certify a separately provisioned child environment. Fix: distinguish an immutable execution
request from the resolved manifest produced during preparation. Add a preparation/ready boundary
that reports actual execution runtime, dependencies, layout hashes, and capabilities before
simulation starts.

B6 — major — Presentation-only edits conflict with source identity and lack a defined re-resolution
input.
Sections: C2.5–C2.7, C5.1–C5.2, C6. A29 remains partial.
A tracked layout.json enters the source bundle; changing its color changes the bundle hash and
therefore the run spec. Yet presentation edits must reuse the simulation. The stored resolved
layout also loses inheritance provenance. Failure: resetting a file color to its code-defined value
cannot be reconstructed from the resolved color alone; treating the changed bundle as new source
triggers the forbidden rerun. Fix: separate execution identity from presentation revisions and
deployment artifacts. Retain layout declarations/layers and physical dependencies, or provide a
build-only preparation operation for re-resolution.

B7 — major — Revision tags do not make local execution inputs immutable.
Sections: C3, C4, C5.1, C6. A29 remains partial.
The spec identifies revisions but does not require local execution to read a frozen copy of their
files. Failure: a run starts under revision R, then lazily imports a helper or reads calibration
data after the user edits it. It produces mixed-revision results tagged R, even if a cancellation
request follows. Fix: execute local and remote runs from immutable source/input snapshots, or
provide equivalent immutable reads. Define working directory and import-path behavior so execution
cannot silently fall back to the editable project.

B8 — major — Final-only visibility contradicts live streaming.
Sections: C1.7, C4, C5.1, C6. A14 remains partial.
C5.1 says a run becomes visible only after artifact publication completes; C6 requires progress,
KPI updates, and trace chunks during execution. Failure: either live HTTP requests cannot access
staging artifacts, or staging becomes implicitly public without the attempt fencing and lifecycle
rules applied to final results. Fix: distinguish provisional attempt visibility from committed
result visibility. Serve only committed chunks from the current attempt, retain their attempt/
revision identity, and define invalidation on cancellation or supersession. Keep final publication
atomic.

B9 — major — Cookie authentication needs instance isolation and a bootstrap exception.
Sections: C6, §9.5; D36. A17 remains partial.
Cookies are scoped by host and path, not port. HttpOnly and SameSite=Strict do not change that.
Failure: two studio instances overwrite a shared cookie name, or another service on the same
loopback host receives the studio credential. Host/Origin checks at the studio do not prevent that
credential from being sent elsewhere. Fix: specify per-instance cookie naming and session scope,
and define whether other loopback services are trusted. If they are not, use an additional origin-
bound secret for privileged operations. Also explicitly exempt the one-time-token bootstrap from
the normal state-changing Origin rule. RFC 6265 (https://www.rfc-editor.org/rfc/rfc6265#section-
8.5)

B10 — major — A source-bundle hash is insufficient as an environment-cache key.
Sections: C5.2. New consequence of A15’s resolution.
The worker creates an environment per source bundle, but dependency groups, extras, and selected
Python runtime can differ without changing that bundle. Failure: concurrent runs synchronize the
same cached environment with different extras; one synchronization removes packages needed by the
other run. Fix: key environments by the complete provisioning specification, including runtime
implementation/version, lockfile, extras/groups, and relevant installation options. Build them
under a lock and treat published environments as immutable while executions use them.

B11 — major — Pausing at the disk bound has no guaranteed recovery path.
Sections: C1.7, C4, C5.1. A13 remains partial.
Backpressure pauses writes when storage fills, but does not define how committed data releases
space. Shipping a growing single-file trace does not inherently free its local prefix. Failure: a
trace exceeds the bound and remains paused forever even though all available chunks were shipped.
Continuing simulation instead would require buffering or dropping events. Fix: define acknowledged,
reclaimable spool storage separately from final artifacts, plus an explicit disk-full outcome when
reclamation cannot help. Report backpressure separately from model stalling, and preserve
cancellation responsiveness.

B12 — major — Wall-clock chunk deadlines need a safe-state qualification.
Sections: C1.2, C1.3, C1.7, C4. A25 remains partial.
The bus is synchronous, but chunks must close within a wall-clock latency even during slow model
code. Failure: a callback changes fields and then computes for minutes before emitting. A
synchronous recorder misses the deadline; a timer snapshotting live entities captures an incomplete
transition. Fix: independently publish already-committed immutable buffers. Build snapshots from
committed replay state or capture them only at defined safe points. Bound publication latency for
completed events, not the time until arbitrary model code produces another event.

B13 — major — Lane vertex indices recreate the silent-rebinding problem.
Section: C2.3. A20 remains partial.
An existing positional address is not necessarily the same element after editing. Failure: lane A–
B–C has an override on vertices (1,2), meaning B–C. Inserting X before B leaves (1,2) valid but
changes its meaning to X–B, so stale-target detection does not fire. Fix: give vertices/segments
persistent identities or bind ordinal addresses to a topology revision. Keep geometric selectors
where spatial reinterpretation is intentional.

B14 — major — A Python position function is not a portable motion description.
Sections: C1.2, C1.7, C7. A23 remains partial.
The viewer must use a speed profile’s position function when available, but traces permit only
primitive wire values and must work offline without Python. Failure: a custom nonlinear Python
function cannot be reconstructed from its name and parameters by the browser. Fix: require a
portable interpolation representation: supported curve primitives or Python-generated keyframes
with declared accuracy. Use exact interpolation only when that representation exists; otherwise
retain the explicitly approximate fallback.

B15 — major — “Terminating” still lacks an executable stopping rule.
Sections: C1.8, C3, C5.5. A9/A10 remain partial.
Terminating experiments count every entity, but the execution contract retains a numeric horizon
and completion-window semantics. The current Router produces arrivals indefinitely. Failure:
stopping at the horizon leaves unfinished entities without completed flow times; waiting for an
empty event queue never terminates. Fix: distinguish fixed-horizon terminating studies with
censoring from finite-population/drain studies with an arrival cutoff and stop condition. Define
the actual observation duration, denominator, and safety limit for each.

B16 — major — Source capture and provenance still cross release boundaries without an owner.
Sections: §4, C1.6, C3, C5.1, C6. New.
SP1 requires a manifest with a model source-bundle hash; SP4 needs immutable source identities for
runs and watching; source bundles are assigned to SP5, and model entrypoints arrive only in SP4.
Failure: SP4 implements a temporary local identity/snapshot mechanism that SP5 later replaces, or
SP1 claims complete reproducibility for plain scripts without enough supplied provenance. Fix:
assign local source/input capture and canonical hashing to SP4, with SP5 adding transport and
remote provisioning. Define SP1’s caller-supplied provenance API and explicitly represent
unavailable provenance.

B17 — minor — Catalog growth needs a seek-time schema lookup rule.
Section: C1.7. A26 remains partial.
Catalog extensions appear before first use, while later chunks are independently decodable.
Failure: seeking directly to a chunk containing a dynamically introduced kind skips the earlier
extension needed to validate or interpret its state. Fix: make chunks self-describing, include
cumulative catalog checkpoints, or index catalog epochs so required definitions are fetched without
replaying the entire prefix. This can remain an SP1 encoding choice, but the independence
requirement must cover it.

B18 — minor — The no-observer invariance test requests outputs that do not exist.
Sections: C1.3, C1.6, §8. A7/A40 remain partial.
The digest is a subscriber and KPIs are observer outputs, yet a run with no observers must produce
both for comparison. Failure: the test quietly installs instrumentation and no longer exercises the
zero-subscriber path. Fix: separate true no-subscriber benchmarks and core-outcome comparisons from
instrumented invariance tests. Compare common declared KPIs when collectors differ. The purity
audit and retention of control accounting in the core are otherwise adequate.

B19 — minor — Field replacement can make collection deltas quadratic, including in KPI mode.
Sections: C1.2, C1.6, C1.9. New.
The stated delta operation replaces a field with its new value. Failure: representing a server
queue as a list serializes 1+2+…+N IDs while it fills; at 50,000 jobs that is about 1.25 billion
IDs before draining. The digest can incur this construction and encoding cost even when no trace is
stored. Fix: require bounded collection updates or normalized membership/order fields, a policy for
oversized individual events, and congested-workload benchmarks. This is an SP1 acceptance risk, not
evidence that the reference size target is impossible.

B20 — minor — Fresh-process cost is absent from the execution budgets.
Sections: C4, C1.9, §7–§8. New.
The benchmark modes measure simulation instrumentation, not startup, imports, provisioning, or
repeated PyPy warm-up. The current Runner (src/simulatte/runner.py:126) amortizes interpreter
startup through a pool. Failure: thousands of short replications spend most of their time starting
fresh execution processes despite acceptable event-bus overhead. Fix: retain isolation, but
benchmark end-to-end replication throughput on CPython and PyPy across short and long runs.
Establish the supported workload envelope before promising efficient experiment execution.

B21 — minor — Process-group cancellation is a POSIX description, not a portable guarantee.
Sections: N5, C4, §7–§8. A13 remains partial.
Windows process creation and termination do not provide the same process-group behavior, and
supervisor-death cleanup needs more than cooperative Python code. Failure: local cancellation kills
the interpreter but leaves a subprocess launched by the model running. Fix: define platform-
specific containment and parent-death behavior, such as Windows Job Objects, and test descendant
cleanup. Where the best-effort Windows support cannot provide it, state the limitation rather than
claiming universal cleanup. Python subprocess documentation (https://docs.python.org/3/library/
subprocess.html#subprocess.Popen.send_signal), Microsoft Job Objects (https://learn.microsoft.com/
en-us/windows/win32/procthread/job-objects)

B22 — minor — Encoder capability does not establish a bounded output path on Safari.
Section: C7.5. A30 remains partial.
VideoEncoder.isConfigSupported() checks encoding, not saving. Safari does not support
showSaveFilePicker, so the straightforward Chromium streaming sink is unavailable there. Failure:
H.264 capability checks pass, but export falls back to accumulating the whole video in memory or
cannot save it. Fix: specify and test the sink separately: for example OPFS staging plus a download
path, a permitted server relay, or a bounded fallback. Include storage exhaustion and cancellation
in that contract. Browser compatibility data (https://github.com/mdn/browser-compat-data/blob/main/
api/Window.json)

B23 — minor — Reset-to-inherited still cannot express explicit removal.
Section: C2.5. A20 remains partial.
Both absence and null inherit; neither removes an inherited optional value. Failure: code defines a
speed limit, but Studio cannot express an unlimited arc because null restores the code limit. Fix:
distinguish inheritance/reset from explicit unset/delete. Whole-list replacement does not solve
optional scalar or map-member removal.

B24 — minor — Clearance inflation can make ordinary boundary ports unreachable.
Section: C2.3. A35’s fix introduces a new gap.
A port on its warehouse boundary lies inside the warehouse’s inflated obstacle when clearance is
positive. Failure: every connector crosses the owner’s exclusion envelope and resolution rejects a
normal loading bay. Using uninflated geometry for all connectors would instead bypass clearance
checks. Fix: define exterior approach ports or owner-specific docking corridors/apertures, while
retaining clearance checks against other obstacles.

B25 — minor — SP1 needs staged feasibility gates before its contracts ship.
Sections: §4, C1, §8. New.
SP1 now combines identity migration, all-component instrumentation, generic deltas, hashing, RNG
migration, collector semantics, logging, persistence, and performance work. The browser reader
arrives two releases later. Failure: extensive migration completes before discovering that the
chosen delta/codec design is too expensive or awkward to consume in TypeScript. Fix: keep one SP1
release if desired, but stage it internally: representative end-to-end events/deltas/digest, a
minimal TypeScript conformance reader, measured budgets, then broad component migration. The scope
is not demonstrably too large for one release; it is too interdependent to postpone those proofs
until SP3.

A-findings resolved at the global-spec level:
A4, A6, A8, A11, A12, A16, A18, A21, A22, A24, A27, A28, A31, A32, A33, A36, A37, A38, A39, A41.

Question requiring Davide’s decision:
Should physical layout values be allowed to determine the number or construction of entities—for
example, floor area determining the number of servers—or should 1.0 restrict them to configuring
entities already declared before resolution? That choice determines how much deferred construction
the revised lifecycle must support.


## Triage (Claude, 2026-10-08)

The cited code facts were checked and confirmed: `examples/intralogistics_simple.py` submits orders before `env.run()`, and `FleetCoordinator.submit()` dispatches immediately. Every finding was **accepted**. Davide answered the question with option (b), allowing layout-dependent construction (D38). Where each is addressed in revision 3:

| Finding | Resolution |
|---|---|
| B1 | C1.6 semantic projection, domain ordinal, initial state (D39) |
| B2 | C2.7 bind before finalize; closure computed at finalize |
| B3 | C2.7 validation against declared definitions; activation with queued pre-activation operations; stages for layout-derived construction (D38) |
| B4 | C2.3 explicit obstacles; C2.6 transitive rule limited to obstacles (D43) |
| B5 | C4 `ready` message; C5.1 execution request versus resolved manifest (D40) |
| B6 | C2.5 layout file outside source identity; C2.7 stored layers; C5.1 result reuse; C6 (D41) |
| B7 | C3 immutable inputs (D42) |
| B8 | C5.1 provisional versus committed visibility |
| B9 | C6 per-instance cookie, per-instance secret, bootstrap exemption |
| B10 | C5.2 environments keyed by provisioning spec |
| B11 | C4 spool with acknowledged reclamation, `backpressure`, `disk_full` |
| B12 | C1.7 latency bound on completed events; safe-point snapshots |
| B13 | C2.3 persistent lane vertex ids |
| B14 | C1.2 portable motion description |
| B15 | C5.5 three experiment types |
| B16 | §4 and C5.2 source capture in SP4; C1.6 caller-supplied provenance (D42) |
| B17 | C1.7 catalog epochs in the index |
| B18 | C1.3 and §8 invariance split |
| B19 | C1.2 bounded collection deltas, oversized events; C1.9 congested workload |
| B20 | C1.9 replication throughput; C4 warm spares (D45) |
| B21 | C4 platform-specific containment |
| B22 | C7.5 output sink contract |
| B23 | C2.5 explicit `$unset` |
| B24 | C2.3 approach points |
| B25 | §4 SP1 feasibility gates (D44) |
