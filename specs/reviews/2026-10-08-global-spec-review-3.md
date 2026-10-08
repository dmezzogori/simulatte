# Global spec review 3 (Astra, Codex GPT-6-Astra xhigh)

- **Date:** 2026-10-08
- **Reviewed commit:** 3a012da
- **Verbatim findings below; triage at the end.**

C1 — major — The network barrier does not freeze all graph-producing inputs.
Sections: C2.3, C2.7.

Problem: The barrier lists obstacles, blocked areas, lanes, grid and clearance, but
omits ports/connectors, explicit nodes/arcs and network overrides. “After the last
stage that declares…” also needs an operational meaning when declarations happen
inside Python callbacks.

Failure scenario: A stage reads the generated network, then creates a non-obstacle
warehouse with a port. This changes none of the explicitly prohibited inputs, yet
connecting the warehouse requires modifying the supposedly frozen graph.

Suggested fix: Define a deterministic barrier, such as immediately before the first
network-reading stage, or an explicit declared boundary. Freeze all graph-producing
inputs there. Subsequent stages may construct entities bound to existing graph
elements, but cannot introduce connectors or alter topology, geometry or arc
attributes. Reject dependencies that require crossing this boundary backwards. The
API can remain deferred to SP2.

C2 — major — Activation has no single initial-state/event boundary. B1 remains
partial.
Sections: C1.1, C1.6, C2.7, C4; D39.

Problem: C1.6 starts the digest with state after activation. C2.7 captures state
during activation, which also executes queued operations. Entity creation already
emits events during construction. These statements do not establish which
transitions belong before versus after the initial snapshot.

Failure scenario: Queued submission creates and dispatches an order. A recorder
captures the resulting initial state and then applies the submission’s creation/
insertion deltas again. Another implementation excludes activation events and loses
the initial dispatch history.

Suggested fix: Establish one explicit boundary: finish preparation, capture initial
state, then execute queued simulation actions as ordinary recorded transitions.
Earlier construction events must either form a defined prelude or be collapsed into
that state. Define the initial cursor/domain ordinal and align ready with this
sequence. This must be a core/SP1 contract, usable before Layout ships in SP2.

C3 — major — Queuing only physical operations changes setup semantics. B3 remains
partial.
Section: C2.7.

Problem: Deferral depends on whether an operation needs physical data, rather than
whether it depends on another deferred operation. Pending-state reads, dependent
commands and partial activation failures are unspecified.

Failure scenario: Setup calls fleet.submit(order); fleet.cancel(order). Submission
is queued, but cancellation executes immediately. The current cancellation code
(src/simulatte/intralogistics/fleet.py:202) finds no mission and marks the order
cancelled; later submission dispatches it anyway.

There is also existing time-zero initialization: fleet construction schedules
starting-position reservations as a SimPy process. Binding alone does not mean
those reservations have completed.

Suggested fix: Define the supported pre-activation command lifecycle: preserve
ordering across dependent submissions, cancellations and updates; specify pending-
state reads; distinguish required initialization from ordinary time-zero events. On
an uncaught queued-command failure, stop activation and fail the attempt, leaving
subsequent commands unexecuted. No rollback guarantee is necessary.

C4 — major — Physical accessors can still make auto-layout affect simulation. B4
remains partial.
Sections: C2.4, C2.6–C2.7.

Problem: Explicit obstacle membership fixes the original network problem, but C2.6
still permits reading any resolved property physically. That contradicts the
guarantee that auto-layout never affects physical properties.

Failure scenario: Stage 0 creates an automatically positioned server. A later stage
reads its resolved x coordinate to determine buffer capacity or server count. This
follows the accessor rules while making the auto-layout algorithm determine
simulation results. Later stage-created entities can also change the automatic
arrangement.

Suggested fix: Reject physical reads of auto-derived values; require explicit code/
file values for those dependencies. Compute presentation auto-layout over the
complete entity set after construction stages finish. Apply the same prohibition
during binding.

C5 — major — Environment reuse can execute an older source snapshot. B10 remains
partial.
Sections: C3, C5.2.

Problem: Environments are immutable and keyed independently of source identity, but
uv sync installs the project and workspace members as editable packages by default.
Official uv documentation (https://docs.astral.sh/uv/concepts/projects/sync/
#editable-installation)

Failure scenario: Snapshot S1 installs S1/src/package into the cached environment.
A source-only edit creates S2 with the same lockfile and provisioning key. Reusing
the environment still imports S1. Adding S2’s project root to the import path does
not redirect a src-layout installation. Non-editable installation instead embeds
S1’s code; resynchronizing would violate cache immutability.

Suggested fix: Either cache only external dependencies and provide per-attempt
installations/overlays for all local packages, or include the content identities of
root, workspace and local-path packages in the environment key. This must agree
with the immutable-source guarantee.

C6 — major — The additional studio secret needs a cookie-independent delivery rule.
B9 remains partial.
Section: C6.

Problem: The page receives the secret in its response body, but the spec does not
prohibit obtaining that page—or reissuing the secret—using only the session cookie.

Failure scenario: Another loopback service receives the studio cookie, which is
possible because cookies are shared across ports. It then makes its own HTTP
request to Studio using that cookie, retrieves the page containing the secret, and
performs privileged operations. Browser origin restrictions do not constrain that
service’s HTTP client. The port-scope premise is documented in RFC 6265 (https://
www.rfc-editor.org/rfc/rfc6265#section-8.5); the retrieval attack follows from the
currently permitted delivery flow.

Suggested fix: Require that cookie possession alone can never retrieve or
regenerate the second secret. Deliver it through the one-time-token bootstrap,
retain it in origin-scoped client state, and define reload/new-tab recovery
accordingly. The exact mechanism belongs in SP4; this security invariant belongs
here.

C7 — major — The new stopping modes are missing from execution identity and KPI
finalization. B15 remains partial.
Sections: C1.6, C1.8, C3, C5.1, C5.5.

Problem: Three experiment types now exist, but execution requests and manifests
still enumerate only horizon and warm-up. They do not carry a normalized stopping
policy, arrival cutoff or drain condition. C1.8 also universally specifies a half-
open observation window.

Failure scenario: A fixed-horizon run and a finite-population run can have
identical listed request fields despite different stopping behavior. A replay
cannot reconstruct the distinction. If a draining run finishes at time T, treating
T as the half-open horizon excludes its final completion; using the safety limit
instead gives time-weighted KPIs the wrong denominator.

Suggested fix: Include the normalized stopping policy and its inputs in requests
and manifests. Define the population/drain predicate and safety limit. Finite-
population finalization must include the draining completion cursor and use actual
elapsed observation time. Remove the remaining blanket “terminating … all jobs”
wording.

C8 — minor — Physical closure must explicitly include structural and negative
dependencies.
Sections: C2.6–C2.7, C5.1.

Problem: Pure-data reuse depends on whether an edit touches the recorded closure,
but the contract does not explicitly cover absent values, defaults or collection
membership. SP2 and SP4 could interpret this differently.

Failure scenario: A closure records properties of existing obstacles but not the
obstacle-selection condition or collection membership. Adding a blocked area or
enabling an existing placement as an obstacle is incorrectly classified as
presentation-only.

Suggested fix: State that the closure includes structural and negative
dependencies: absence/default checks, collection membership and selection
predicates. Adding, removing or enabling physical inputs must invalidate reuse.
Representation details can remain in SP2. B6’s stored-layer resolution otherwise
stands.

C9 — minor — Watching only imports observed during build misses stage code.
Sections: C2.7, C6.

Problem: Stages and binding now execute model logic after build, but the watcher
contract still discovers project-local imports only while build runs.

Failure scenario: A stage lazily imports sizing.py to determine entity counts.
Editing that file changes construction, but Studio does not rerun. Immutable
snapshots prevent mixed-version execution; they do not detect that the displayed
result is stale.

Suggested fix: Align watching with source-snapshot identity, including relevant
additions/removals, plus declared inputs. Do not restrict coverage to imports
observed during build. The watcher implementation remains SP4’s choice.

C10 — minor — Identical-manifest reproducibility is still stated unconditionally.
Sections: C1.5–C1.6, C5.1, §9.2.

Problem: B16 correctly permits unavailable provenance, and unmanaged randomness
remains supported, yet C1.6 promises identical canonical outputs whenever manifests
match. Observer configuration also resides partly in execution requests.

Failure scenario: Two scripts with unavailable source identity produce matching
manifests but different trajectories. Separately, different recording or collector
configurations can produce different complete artifacts despite identical semantic
behavior.

Suggested fix: Condition guaranteed semantic reproducibility on sufficient
provenance and managed randomness. Complete-artifact determinism additionally
requires matching recording/observer configuration. Partial manifests still support
reporting provenance limitations and checking observed rerun fingerprints.

Resolved B-findings: B2, B5, B6, B7, B8, B11, B12, B13, B14, B16, B17, B18, B19,
B20, B21, B22, B23, B24, B25.

The sub-project order and SP1 feasibility gates are now coherent. The remaining
preparation boundary should be owned by SP1 and consumed by later projects.

Questions for Davide: None requiring a new product decision. The fixes above
preserve D38.


## Triage (Claude, 2026-10-08)

Cited code facts checked and confirmed: `FleetCoordinator.cancel()` on an order that is not pending and has no mission marks it cancelled, so a later `submit()` would dispatch it; the coordinator constructor schedules `_initial_placement()` as a process. Every finding was **accepted**. Where each is addressed in revision 4:

| Finding | Resolution |
|---|---|
| C1 | C2.7 network barrier freezing all graph-producing inputs |
| C2 | C1.10 preparation prelude, initial state, `ready` alignment (D46) |
| C3 | C1.10 single command queue for all deferrable commands, pending reads, initializers, failure semantics (D46) |
| C4 | C2.4 and C2.6: physical reads of auto-layout values rejected; auto-layout computed after the last stage |
| C5 | C5.2 cached environments hold external dependencies only; local packages from the snapshot, verified (D47) |
| C6 | C6 cookie alone never yields the secret; bootstrap-only delivery |
| C7 | C1.6, C1.8, C3, C5.1, C5.5: normalized stopping policy, windows per policy, drain predicate |
| C8 | C2.6 structural and negative dependencies |
| C9 | C6 watching covers the whole source snapshot |
| C10 | C1.6 conditional reproducibility guarantee |
