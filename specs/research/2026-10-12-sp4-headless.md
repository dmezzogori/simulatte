# SP4 headless coordinator and CLI evidence

This records Task 7 integration on eva (macOS arm64). It does not close native
Windows/Linux acceptance or the remaining Studio tasks. No release version has
been changed; local wheel builds still carry the repository's 0.15.0 metadata.

## Implemented boundary

The coordinator owns source/input capture, immutable prepared metadata, typed
inspection assignments, actual runtime evidence, one current/one pending local
assignment, durable segment receipts and the result visibility transaction.
Inspection and execution both use disposable children. Reopening a stored request
does not inspect the current model or parse the current pyproject. The run and
reproduce commands share this coordinator and have no web dependency.

Large request, layout, runtime and model-info maps are verified data documents;
worker control messages carry bounded references. Every execution requires measured runtime evidence and verifies it before import
and after execution; there is no fallback that relabels requested values as
observed facts. Distribution RECORD hashes identify
installed manifests, not a complete snapshot of external files. Missing records
remain explicit provenance warnings and prevent a verified reproduction claim.

`simulatte run` accepts typed parameters, seed, stopping overrides, KPI selection,
trace level and timeout. It emits one JSON result envelope, or a human result on
stderr. `--output` creates exclusively and removes only its own partial export on
failure. `--reproduce` compares the committed baseline's fingerprint and artifacts
and reports incomplete provenance separately from a mismatch. No automatic retry,
provisioning, experiment or remote execution path was introduced.

## Failure evidence

- Working model/data edits after capture cannot change a run; reproduction still
  works after reopening the store and deleting the working model.
- CPU-bound cancellation and supersession cannot publish an obsolete result.
- Capture/queue/materialization deadlines are checked before dispatch; source
  capture expiration cannot create a new inspection revision.
- A full disk during publication never creates a committed result. If even the
  failure-state transaction cannot be written, the live error explicitly says
  the terminal state is not durable; startup recovery owns the orphan transition.
- Malformed metadata, wrong assignment generations, worker EOF/heartbeat loss,
  premature results, corrupt receipts, and changed runtime evidence fail closed.
- INITIAL/ready disagreement and malformed final result/resolved schemas cannot
  publish. A readiness notification can wait for its durable metadata receipt.
- A receipt ACK can arrive after a failed child closes its private channel.
  Deterministic tests proved the original supervisor masked that validation error
  with a transport error. It now drains the terminal proposal before classifying
  loss; both late ACK and late readiness ACK are covered.
- Retrying shutdown after a join timeout releases the store owner only after
  the actor exits; an earlier implementation retained the lock indefinitely.
- A fresh wheel smoke exposed macOS `/var` versus `/private/var` project discovery.
  Parent-path normalization now preserves the model filename, with a regression
  for a symlinked project parent.

## Verification

The initial regression runs passed 2,711 and then 2,784 tests, but missed the 99%
coverage gate. These are diagnostic runs, not acceptance passes. Additional tests
cover the missing failure boundaries. Final checks are recorded below when done.

A rebuilt core-only wheel was installed into an isolated CPython 3.11 environment.
The smoke runs outside the checkout, asserts Starlette/Uvicorn/watchfiles/websockets
are absent, removes uv from PATH, executes a model, verifies its exported trace,
deletes the model and successfully reproduces the stored request. Frontend assets
were regenerated through the existing build/package commands; the packaging
freshness check was not bypassed.

Earlier targeted compatibility run: 121 passed on CPython 3.11 and 121 on PyPy
3.11. The final supervisor/cancellation fixes receive a subsequent targeted run.
Ruff and ty checks include the new execution tests and examples as well as
production code. The production-shop example works as a plain script. CLI runs
of the horizon, finite-count and finite-cutoff examples verify their traces and
end at 120.0, 8.5 and 7.5 respectively. The finite example also runs from this
repository, exercising capture of Simulatte itself.

## Remaining scope

Revision-aware input ownership and watcher integration, live trace extensions,
authenticated Studio websocket/server routes, parameter/layout editors, run/view
history and pinned exports remain Tasks 8–12. Native platform, saturation,
performance and browser evidence remain Task 13. Full user documentation and
final distribution acceptance remain Task 14. This evidence does not claim that a
mocked Windows Job test proves native descendant containment.

## Final headless integration gate

- Fresh full Python run: **2,812 passed, one native-Windows skip, 99.04% branch
  coverage**. Existing Starlette/httpx test-client deprecation warning remains;
  no dependency migration is bundled into this work.
- CPython 3.11 and PyPy 3.11: 149 targeted tests passed on each; after the final
  runtime requirement and shutdown fix, 67 affected tests passed on each.
- Ruff lint/format and ty pass, including execution tests and both examples.
- Rebuilt final core-only wheel: run, export, trace verification and reproduction
  pass outside the checkout with no web dependencies and no uv on PATH.
- Existing frontend build and asset packaging complete. No frontend source was
  changed in this integration batch; SP3 reader/export compatibility evidence is
  in the preceding execution-foundation report.

The local headless integration gate is met. The broader platform/failure matrix
and all remaining Studio tasks above remain open.
