# Global spec review 1 (Astra, Codex GPT-6-Astra xhigh)

- **Date:** 2026-10-08
- **Reviewed commit:** 283c08d
- **Verbatim findings below; triage at the end.**

A1 — blocker — Physical freezing precedes reliable entity binding.
Sections: C1.1, C2.5, C2.7.
The lifecycle cannot reliably distinguish valid future entities from orphans when physical data freezes. Existing builders construct the
graph before warehouses, because warehouses require concrete bay nodes (src/simulatte/intralogistics/builders.py:50). Failure: a
warehouse override either gets discarded before its warehouse exists, or its stale footprint changes the frozen network before final
orphan detection. Fix: introduce declarations of physical entity IDs/kinds before graph resolution, followed by construction and final
binding validation. Preserve early freezing, but make its prerequisites explicit.

A2 — major — Physical hashing lacks a dependency contract.
Sections: C2.3, C2.6, C2.7.
Physical effects depend on more than directly classified properties. Lattice connectivity depends on footprints, rotations, grid spacing,
positions, and ports. Meanwhile, “anything else a model reads” cannot be determined by a fixed schema without a declaration mechanism.
Failure: changing a nominally presentation-only footprint changes connectivity while retaining the physical hash; alternatively, the
displayed footprint changes while the frozen network does not. Fix: declare physical dependencies transitively, including custom model
reads. A constant physical hash should mean “no physical dependencies,” not “production model.”

A3 — major — The fingerprint cannot validate trajectory reproduction.
Sections: C1.6, C5.4; D17.
Hashing KPI scalars and event count detects only differences in those summaries. Failure: two dispatch sequences produce identical
throughput, mean completion time, and event counts but different queues and motion. The animation rerun passes validation while depicting
a different trajectory. Fix: compute a canonical semantic-event digest in both kpi and full modes, independent of log filtering,
collector selection, and recording level. Alternatively, explicitly label the existing comparison as aggregate agreement, without
claiming verified reproduction.

A4 — major — The reproducibility identity omits material inputs.
Sections: C1.6, C1.7, C5.2, §9.2.
Simulatte version, model source hash, parameters, seed, layout, and platform do not identify Python implementation/version, dependency
versions, imported project modules, datasets, or relevant environment configuration. Python itself permits changes to most random-
generation algorithms between versions. Failure: the same model/header runs under a different Python or NumPy version and produces
different results on the same host. Fix: define a reproducibility manifest containing runtime, resolved dependencies, RNG algorithm/
version, source bundle, and declared external inputs; retain those artifacts when reproducibility is promised. Python documentation
(https://docs.python.org/3/library/random.html#notes-on-reproducibility)

A5 — major — Replay needs a state-transition contract, not just an event catalog.
Sections: C1.2, C1.7, C7.
Schemas describe event structure, but not how events reconstruct state or when snapshots are consistent. Current processing completion,
after-operation hooks, resource release, and completion callbacks are distinct phases (src/simulatte/shopfloor.py:1064). Failure: a
snapshot says a server is occupied while the next replayed event assumes it was released; a custom event is decoded but cannot update the
generic entity. Fix: specify transition boundaries, snapshot sequence watermarks, and versioned reducers or canonical state deltas.
Define what generic replay guarantees for custom events.

A6 — major — Synchronous bus delivery is underspecified around derived events and mutation.
Sections: C1.2–C1.4, C1.8.
KPI collectors may emit KPI events while processing another event. Frozen dataclasses also do not freeze nested dictionaries or lists.
Failure: a collector receives event 10 and recursively emits event 11; a recorder subscribed later receives 11 before 10. Another
subscriber mutates a payload before recording. Fix: define FIFO handling of nested emissions, deterministic subscriber ordering,
subscription-change semantics, and deeply immutable or defensively copied payloads. Give derived events an explicit ordering rule.

A7 — major — Existing observational reads violate the intended purity rule.
Sections: C1.3, C1.8, §9.1.
The intralogistics collector calls agv.utilization(), which updates state_durations and _state_entered_at (src/simulatte/intralogistics/
agv.py:104). Failure: changing snapshot or collection frequency changes accumulator updates and potentially exact KPI values. Separately,
moving control-relevant accounting into optional collectors would change behavior: RR dispatching reads server utilization. Fix: make
observational getters pure, retain behavioral accounting in the core, and explicitly require identical semantic outcomes across observer
configurations. This needs a migration audit, not merely moving callbacks onto the bus.

A8 — major — RNG binding is missing where the current API actually shares distributions.
Sections: C1.5, C3, §7.
A zero-argument distribution cannot discover its component-specific stream reliably. Scenario currently shares a family’s distribution
object across servers and can reuse it across environments (src/simulatte/scenario.py:309). Failure: attaching an RNG to that shared
object leaks state between environments or lets the last binding determine every server’s stream. Fix: keep distribution descriptions
immutable and create environment-bound samplers explicitly. Define stream namespaces, stable derivation, component identity, and the
behavior of custom sampler callables.

A9 — major — Warm-up deletion does not define the KPI estimand.
Sections: C1.8, C3, C5.4.
An interval alone does not define utilization, flow time, throughput, or EMA semantics. Current server work is credited only when
processing completes (src/simulatte/server.py:285). Failure: an operation spanning both warm-up and horizon contributes zero or its
entire duration instead of the interval overlap; completed-job means mix pre-warm-up arrivals with later arrivals. Fix: define each KPI’s
observation unit, cohort, denominator, censoring policy, boundary clipping, EMA reset policy, empty result, and finalization. Also
reconcile [warmup, horizon] with SimPy’s exclusion of events scheduled exactly at numeric until. SimPy documentation (https://
simpy.readthedocs.io/en/latest/topical_guides/environments.html#simulation-control)

A10 — major — Experiment statistics lack replication and missing-data semantics.
Sections: C5.4.
“T-based intervals” and “paired comparisons” leave important choices open. Failure: jobs or time samples are treated as independent
observations, failed replications disappear from the denominator, or configurations are paired by completion order after remote retries.
Fix: make independent replication-level estimates the default observations; pair differences by replication identity; report actual
sample counts, failed/missing pairs, and insufficient-sample states. Distinguish terminating from steady-state experiments, and identify
grid-wide comparisons as pointwise unless simultaneous inference is implemented.

A11 — major — Loading source anew does not provide a fresh interpreter.
Sections: C4.
A long-lived process retains imported dependencies, module globals, threads, environment changes, logging handlers, and native-library
state even when the model module is loaded under a new name. Failure: a second replication inherits a dependency cache or background
thread from the first and depends on execution order. Fix: define the worker as a supervisor with a fresh execution subprocess per run.
Process reuse should require a separately specified reset contract and equivalence evidence; source-hash equality alone is insufficient.

A12 — major — User stdout can corrupt the worker protocol.
Sections: C4.
The same process executes arbitrary Python and writes framed data to stdout. Failure: print() during import or simulation inserts text
into a frame header; a dependency or native extension writes directly to file descriptor 1 despite a Python-level redirect. Fix: isolate
protocol output from model stdout at the process/file-descriptor boundary. Capture stdout and stderr as bounded diagnostics with run
identity. Shell/bootstrap output must also remain outside the framed channel.

A13 — major — Cancellation, liveness, and backpressure need an execution contract.
Sections: C4, C5.1, C6.
Messages alone do not make a synchronous simulation responsive. Failure: an infinite loop in build, a long callback, or a blocked trace
write prevents the worker from reading cancel or sending heartbeats. Killing the local SSH client may leave remote work running. Fix:
keep control handling outside the execution process; define heartbeat and timeout behavior, cooperative cancellation followed by process-
tree termination, disconnect cleanup, bounded buffering, and slow-consumer behavior. Distinguish process liveness from simulation
progress.

A14 — major — Run identity, retry identity, and artifact publication are conflated.
Sections: C5.1–C5.4.
(experiment config, seed) does not explicitly include source/runtime changes, horizon, warm-up, or artifact requests. Ignoring duplicate
results also provides no execution-level idempotency. Failure: a late result from a superseded source version wins; a full rerun is
discarded as a duplicate of its KPI run; rescheduling repeats model side effects. Fix: define immutable run specifications, separate
attempt IDs and artifact identities, fence stale attempts, and publish results atomically after durable artifacts. State that execution
retries are at-least-once and require retry-safe models or an explicit retry policy.

A15 — major — Remote bootstrap and environment creation are incomplete and partly contradictory.
Sections: C4, C5.2; D19–D20.
A host with only SSH and uv cannot initially execute simulatte worker. Also, uv sync installs software, contradicting the blanket
statement that runs never do so implicitly. --frozen skips checking whether the lockfile matches project metadata. Failure: first
execution has no worker executable, or a changed dependency declaration runs against a stale lock. Fix: specify bootstrap-before-
handshake, the required Simulatte version, Python selection, extras/groups, and lock validation. Clarify that explicit setup governs
installing uv, while runs authorize isolated runtime/dependency provisioning. uv documentation (https://docs.astral.sh/uv/concepts/
projects/sync/)

A16 — major — .gitignore does not define a complete or safe source bundle.
Sections: C5.2, §9.2, §9.5.
Ignored files may be required datasets; nonignored files may contain secrets. Workspace members, external path dependencies, symlinks,
private indexes, and native system dependencies are also unaddressed. Failure: a local run reads ignored calibration data that is absent
remotely, or an unrelated credential file is transferred automatically. Fix: define an explicit bundle/input manifest, inclusion
overrides, dependency preflight, archive path and symlink rules, size limits, immutable staging, and content verification. Unsupported
project structures should fail before scheduling.

A17 — major — The studio security contract stops before its important trust boundaries.
Sections: C6, C7, §9.5.
Loopback binding, a token, and an Origin check do not specify handling of untrusted trace strings, HTML exports, file paths, or token-
bearing URLs. Failure: an unsafe trace label/export payload executes JavaScript in the studio origin and uses its authority to start code
execution; an unrestricted layout-write path modifies another file. The non-loopback flag also expands scope beyond the stated local-only
threat model. Fix: define exact Host/Origin/authentication rules, token bootstrapping and redaction, text-only rendering and safe export
embedding, project-scoped file access, and parser limits. Remove network binding from 1.0 or specify its additional security
requirements.

A18 — major — Generated IDs can silently rebind physical overrides across configurations.
Sections: C1.1, C2.5, C5.4; D12.
Kind checks do not detect reuse of the same ordinal by a different entity. Failure: configuration A constructs optional warehouse X
before Y; configuration B omits X, so X’s warehouse-0 override now applies to Y. No orphan appears, and no new drag triggers the naming
warning. Fix: require stable names for persisted physical overrides, or bind generated-ID overrides to a compatible construction/
configuration manifest. Optional names can remain for ordinary scripts.

A19 — major — Construction-time registration conflicts with environment-free definitions.
Sections: C1.1, C2.3, §7.
Node and LayoutGraph currently have no environment; TransferOrder also lacks an explicit environment field (src/simulatte/intralogistics/
graph.py:12, src/simulatte/intralogistics/order.py:25). Failure: registering a reusable graph’s nodes at construction binds them to the
wrong environment or makes the preserved explicit-graph API impossible. Fix: distinguish immutable definitions from per-environment
bindings, with attachment-time registration where appropriate. Define environment-consistency checks for orders and references.

A20 — major — The layout file cannot represent all promised edits under its current contract.
Sections: C2.3–C2.5.
Overrides keyed only by entity ID do not naturally address grid settings, blocked regions, lanes, arc selections, or deletion of code-
defined properties. Current arcs have no identifier. Failure: adding a blocked rectangle has no valid entity key; regenerating a lattice
makes a previously selected arc refer to something different. Fix: separate global/network overrides from entity placements, define
stable generated-element addressing and stale-target handling, and specify omission, deletion, reset-to-code, and list replacement
semantics.

A21 — major — Explicit graphs need one authoritative resolved representation.
Sections: C2.3, C2.7, §7.
Graph overrides must reach fleet routing, Euclidean distances, warehouse bays, AGV initial nodes, and traffic resources together. Nodes
are frozen value objects whose coordinates affect equality and hashing; traffic resources are created from those objects (src/simulatte/
intralogistics/traffic.py:78). Failure: the viewer shows a moved node while the fleet uses the old one, or replacing only graph nodes
breaks traffic-resource lookups. Fix: require all consumers to use the same resolved immutable physical graph, materialized before their
construction.

A22 — major — Production auto-layout cannot generally discover routing during build.
Sections: C2.4, C2.7, §9.1.
Router routings are arbitrary callables, invoked after arrival time advances; default routing consumes randomness (src/simulatte/
router.py:132). Failure: auto-layout calls a routing callback to infer topology, changing simulation RNG/state; alternatively, it freezes
before any route exists. Fix: provide optional topology declarations and a deterministic fallback for unknown topology. Explicitly forbid
executing simulation callbacks for layout discovery.

A23 — major — Motion plans need semantics matching the current fleet abstraction.
Sections: C1.2, C1.7, C7.
The fleet reserves the next node before travel, advances current_node only after travel, and leaves it at the previous node on
interruption (src/simulatte/intralogistics/fleet.py:667). SpeedProfile exposes duration, not position over time. Failure: animation
starts during a traffic wait, interpolates the wrong acceleration profile, or claims a mid-edge interruption position that the simulation
never represented. Fix: define motion start after permission, interpolation fidelity, interruption discontinuities, and active-plan
snapshot state. Do not silently change physics to make animation smoother.

A24 — major — Snapshotting every registered entity threatens the trace-size target.
Sections: C1.1, C1.7.
Completed jobs remain in ShopFloor.jobs_done, while the new registry has no retirement rule (src/simulatte/shopfloor.py:1097). Failure:
each successive chunk repeats every historical job and its timing history; snapshot cost grows with total completed work rather than
current scene size. Fix: separate Python object retention from trace liveness. Define retirement/tombstones, historical lookup, and
snapshots containing active entities plus bounded summary state. Benchmark registry and snapshot memory as well as compressed file size.

A25 — major — Simulation-time windows alone do not ensure streaming or bounded seeks.
Sections: C1.7, C4, C6.
A time window can contain millions of events or take minutes of wall time to finish. Several chunks may also end at the same simulation
time. Failure: the browser sees no complete chunk during a long computation, or a nominally short window takes seconds to replay. Fix:
require chunk limits by events/bytes as well as time, bounded publication latency, and (time, sequence) boundaries. Define how readers
discover committed chunks and indexes while appending, and distinguish clean completion from cancellation or truncation.

A26 — major — Cross-language format and evolution requirements need stronger invariants.
Sections: C1.2, C1.7, C7, §8.
Primitive payloads and schema versions do not define integer ranges, nonfinite floats, nullability, map keys, or reader compatibility.
Nonfinite durations are possible today (src/simulatte/intralogistics/speed.py:49). The header catalog also has no rule for event types
first registered during a run. Failure: Python writes values the browser cannot represent, or an unknown state-changing event is silently
skipped and replay becomes wrong. Fix: define canonical wire types, required versus optional features, supported-version behavior, and
either pre-run catalog sealing or schema-extension records.

A27 — major — Playback time alone cannot express all promised visual states.
Sections: C1.2, C7; N4.
Several transitions can occur at the same t, and production transfers have zero simulation duration. Failure: render(t) cannot
distinguish successive same-time queue/release/start events for debugging; a visible transfer either disappears or visually overlaps
processing at the destination. Fix: define an event cursor such as (t, seq) for stepping and an explicit presentation policy for zero-
duration transfers. Decorative animation must not be mistaken for authoritative entity location.

A28 — major — render(t) lacks an asynchronous preparation and clock-ownership contract.
Sections: C1.7, C7.
Seeking may require HTTP range reads, decompression, replay, and asset loading. The scene also owns a ticker while exporters
independently step time. Failure: export captures a frame before its chunk is ready, or the interactive ticker advances state during
export; two comparison scenes drift. Fix: separate asynchronous seek/preparation from rendering a ready state, define one playback-clock
owner, and provide a manual deterministic mode. State whether determinism means scene state or pixel-identical output.

A29 — major — File watching needs revisions and immutable run inputs.
Sections: C5.1, C6.
Watching a model file and layout.json misses imported helpers and declared data. Multiple tabs, editor writes, and canceled runs can
race. Failure: a helper changes without rerunning; an older result replaces a newer one; two tabs overwrite each other’s layout edits.
Presentation-only edits also unnecessarily cancel expensive simulations under the current blanket rule. Fix: snapshot inputs per run,
attach revision IDs to commands/results, reject stale writes/results, watch declared dependencies, and distinguish physical changes from
presentation changes. Define invalid-file handling and atomic saves.

A30 — major — MP4 and standalone HTML need explicit support contracts.
Sections: C7, §9.3.
WebCodecs availability does not guarantee a usable encoder configuration, and encoded chunks still require container muxing. Failure: an
otherwise supported browser plays traces but cannot export MP4; long exports exhaust memory; exported HTML depends on fonts, icons, or
dynamically loaded assets left on the server. Fix: specify tested codec/container configurations, capability checks and fallback
behavior, bounded encoding/muxing, cancellation, and export contents. Verify HTML with networking disabled and MP4 independently of
interactive playback. WebCodecs specification (https://www.w3.org/TR/webcodecs/), Chrome implementation guidance (https://
developer.chrome.com/docs/web-platform/best-practices/webcodecs)

A31 — major — Release and packaging boundaries contain unresolved dependencies.
Sections: §4, C5.1, §6.
SP3 includes a static server command before the SP4 server layer; SP4’s coordinator owns a run store listed as SP5 work. Frontend assets
copied only during wheel building leave the sdist-to-wheel path unclear. Failure: a standalone SP3 release lacks its serving dependency,
or installing from an sdist unexpectedly requires Node or yields missing assets. Fix: assign the minimal static-serving and run-store
pieces to the releases that consume them. Define and test wheel installation and wheel construction from the published sdist. Clarify
that Python extras cannot make files within one wheel optional.

A32 — major — The performance budget does not cover normal observer configurations.
Sections: C1.3, C1.4, C1.8, C1.7, §8.
Today Environment creates a logger by default; after unification, logging and KPIs are subscribers. A global env.tracing guard could
therefore construct every event during ordinary use. Failure: the zero-subscriber benchmark passes while typical scripts and KPI-only
experiments regress substantially. Fix: define event-type interest checks and benchmark disabled, default logging, KPI-only, and full
tracing modes. Specify workload, logging level, reference hardware, memory, sustained recording throughput, and seek percentile. The 100
MB/200 ms targets are plausible hypotheses, not yet evidence-backed budgets.

A33 — minor — Named streams enable CRN but do not guarantee alignment or variance reduction.
Sections: C1.5, C5.4; D23.
Rejection sampling and routing-dependent draw counts break correspondence. Failure: the current truncated-Erlang algorithm at seed 1
consumes ten primitive draws for its first accepted sample with cutoff 1, versus two with cutoff 3; later samples diverge. Fix: qualify
the claim and document its assumptions. For stronger coupling, use logical job/operation substreams or indexed variates. Paired intervals
remain valid without beneficial CRN; variance reduction is what is not guaranteed.

A34 — minor — Determinism tests contradict volatile metadata and overlook ordering.
Sections: C1.6, C1.7, §8.
Byte-identical traces conflict with mandatory wall-clock start metadata. Current traffic conflict logging also converts sets to lists.
Failure: identical seeded runs have different headers or log payload order. A read-only probe using the existing Node type produced
different orders under PYTHONHASHSEED=1 and 2. Fix: compare canonical deterministic content, classify volatile fields, and audit
observable collection ordering. Run determinism checks in fresh processes with different hash seeds.

A35 — minor — Network generation has an unsafe connector exception and an undefined clearance model.
Sections: C2.1–C2.3.
“Connect each port to its nearest lattice node” does not require a collision-valid connector. Footprint exclusion also does not state
whether AGVs are points or occupy physical width. Failure: a connector crosses a thin wall, or a generated aisle admits a vehicle wider
than the aisle. Fix: require valid port attachments with diagnostics when none exist; explicitly choose point-agent semantics or
clearance-aware geometry. Define diagonal corner-crossing behavior.

A36 — minor — External-store subscriptions do not necessarily bypass React rendering.
Sections: C7; D14.
If “external-store subscriptions” means React’s useSyncExternalStore, changed snapshots trigger component renders. Failure: a 60 Hz time
snapshot still renders every subscribed tile and inspector field. Fix: distinguish imperative subscriptions from React subscriptions,
define ownership and cleanup of imperatively managed DOM, and measure update rates. Keep React/PixiJS; this does not justify revisiting
the framework decision. React documentation (https://react.dev/reference/react/useSyncExternalStore)

A37 — minor — Fixed float precision can change physical inputs during persistence.
Sections: C2.5, C2.6.
Readable formatting is not automatically lossless serialization. Failure: opening and saving an exact-coordinate layout rounds a port
onto a boundary, changes connectivity, and changes results without an intentional physical edit. Fix: require round-trip-safe numbers, or
make quantization an explicit modeled operation applied before resolution and hashing. Define normalization of equivalent values for
hashes separately from display formatting.

A38 — minor — Builder naming needs a composition namespace.
Sections: C1.1, C2.5.
Global id = name, duplicate rejection, and meaningful builder defaults conflict when multiple systems share an environment. The
intralogistics builder already uses fixed names such as WH-A and N1. Failure: constructing two otherwise independent systems produces
collisions. Fix: give builders an explicit namespace/prefix and distinguish display names from registry keys. Document whether kind
participates in identity and how user names interact with generated-name ranges.

A39 — minor — Time units are absent from the cross-language contract.
Sections: C1.2, C1.7, C2.1, C7.
Coordinates are meters, but simulation time is not universally seconds in current production models. Failure: a model using hours is
displayed or exported with second-based labels and playback assumptions; a speed limit is interpreted inconsistently. Fix: record the
simulation time unit or explicitly declare unitless time, and define speed and playback conversions. Avoid retroactively imposing seconds
on generic production models.

A40 — minor — The test strategy misses invariance and failure boundaries, and proposes a potentially flaky statistical check.
Sections: §8.
Round-trips can preserve the same bug in both implementations; a correct nominal confidence interval need not contain the analytic mean
on every run. Failure: CI intermittently fails correct statistics while never detecting recording-induced changes, same-time seek errors,
stale results, or partial artifact publication. Fix: add observer-mode invariance, uninterrupted-versus-seek equivalence, fresh-process
determinism, malformed/truncated trace cases, worker death/cancellation/backpressure, and packaged-install smoke checks. Test statistical
formulas deterministically and coverage over controlled ensembles rather than requiring one interval to contain the truth.

A41 — minor — Playback-synchronized scalar KPIs are ambiguous.
Sections: C1.8, C7.
A final scalar has no time-dependent value unless Python also records its evolving estimate. Failure: playback at time zero shows final
throughput or final mean flow time beside an empty plant, suggesting those results already occurred. Fix: distinguish final-run summaries
from values “as of playback time,” and require Python-generated prefix samples for the latter. Define interpolation and missing-value
behavior for series.

The strongest parts that should not change are:

• Record/replay as the foundation, with live control deferred.
• Explicit component events and separation of observers from behavioral hooks.
• Environment-owned RNGs and Python-owned KPI computation.
• Per-property layout precedence, separate physical/presentation concerns, and explicit-graph support.
• A framework-independent scene shared by studio, static viewing, and export.
• One worker protocol across local and SSH transports, with independently useful releases.

Questions that materially affect the fixes:

1. Must a full rerun reproduce the original trajectory, or is aggregate agreement sufficient if clearly labeled?

2. What should the default flow-time cohort be: jobs arriving after warm-up, jobs completing after warm-up, or a separately defined
   terminating experiment?

3. May custom models consume arbitrary layout properties as physical inputs, or must those dependencies be declared?

4. Which browser/OS combinations must support MP4 export, and what fallback is acceptable?

5. Are automatically retried models required to keep external side effects within attempt-specific output directories?


## Triage (Claude, 2026-10-08)

Code facts cited by the review were checked and confirmed: `intralogistics/builders.py` builds the graph before warehouses; `AGV.utilization()` flushes state; `Scenario.router` shares one service-time distribution across servers; `Server` credits `worked_time` at operation completion; the router draws from the global `random`.

Every finding was **accepted**. Where each one is addressed in revision 2 of the spec:

| Finding | Resolution |
|---|---|
| A1, A21 | C2.7 declare/resolve/bind; one frozen graph (D27) |
| A2 | C2.6 transitive physical classification |
| A3 | C1.6 semantic digest in the fingerprint (D31) |
| A4 | C1.6 run manifest |
| A5 | C1.2 state deltas, transition boundaries (D28) |
| A6 | C1.2 deep immutability; C1.3 delivery order |
| A7 | C1.3 observer invariance and purity audit |
| A8 | C1.5 descriptions versus samplers |
| A9 | C1.8 estimand, window, clipping |
| A10 | C5.5 replication-level observations, pairing, pointwise intervals |
| A11, A12 | C4 supervisor plus execution process (D29) |
| A13 | C4 liveness, cancellation, backpressure |
| A14 | C5.1 run specs, attempts, fencing, publication |
| A15 | C5.2 bootstrap, `uv sync --locked`, provisioning authorization |
| A16 | C5.2 explicit bundle manifest, preflight (D37) |
| A17 | C6 and N8: loopback only, cookie exchange, Host/Origin, text-only rendering (D36) |
| A18 | C1.1 names required for physical overrides (D30) |
| A19 | C1.1 definitions versus bindings |
| A20 | C2.3 stable addressing; C2.5 sections and override semantics |
| A22 | C2.4 static topology only |
| A23 | C1.2 motion semantics |
| A24 | C1.1 lifecycle; C1.7 live-only snapshots |
| A25 | C1.7 chunk limits, commit protocol, footer |
| A26 | C1.2 wire types; C1.7 catalog growth, reader compatibility |
| A27 | C1.2 event cursor; C7.4 zero-duration transfers |
| A28 | C7.1 prepare/render split, single clock |
| A29 | C6 revisions and watching; C3 declared inputs |
| A30 | C7.5 export contracts (D35) |
| A31 | §4 and §6: static serving in SP3, minimal store in SP4, assets in sdist |
| A32 | C1.3 interest checks; C1.9 budgets per mode |
| A33 | C1.5 CRN claim qualified |
| A34 | C1.6 volatile fields; ordering audit; §8 fresh-process tests |
| A35 | C2.3 point vehicles plus clearance; connectors; corner cutting |
| A36 | C7.2 imperative updates, not `useSyncExternalStore` |
| A37 | C2.2 snapping only on edit; C2.5 round-trip numbers |
| A38 | C1.1 global ids, labels, builder prefixes |
| A39 | C2.1 time units |
| A40 | §8 testing additions |
| A41 | C1.8 values over playback versus final results |

Review questions, answered by Davide (all as recommended): 1 → trajectory digest (D31); 2 → completion cohort (D32); 3 → declared through the accessor (D33); 4 → Chromium and Safari required, WebM and PNG fallbacks (D35); 5 → retry-safe models (D34).
