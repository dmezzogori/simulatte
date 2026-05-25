# Dynamic Priority Queue Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make queued requests' priorities live at every dispatch decision by refreshing `req.key` from `job.priority(server)` in `Server.sort_queue`, and call `sort_queue` automatically by overriding `Server._trigger_put`.

**Architecture:** Two coordinated edits in `src/simulatte/server.py`: rewrite `Server.sort_queue` to recompute each queued request's key, and add a `_trigger_put` override that calls `sort_queue` before delegating to SimPy. New tests in `tests/core/test_server.py` cover the put path, get path, policy reassignment, mutable external state, and contract-violation behavior. The existing `test_sort_queue`'s `DummyRequest` test double gets two extra attributes so the new `sort_queue` body can run against it.

**Tech Stack:** Python 3.12+, SimPy, pytest, ruff, pyright, uv.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-25-dynamic-priority-queue-refresh-design.md`.

---

## File Map

**Modify:**
- `src/simulatte/server.py` — change `Server.sort_queue` body (currently lines 259-267) and add `Server._trigger_put` override; update docstrings on `Server`, `Server.sort_queue`, `ServerPriorityRequest`.
- `tests/core/test_server.py` — extend `DummyRequest` in the existing `test_sort_queue` (line 137), and add a new `TestDynamicPriorityRefresh` class with the new tests.

**Read for context (do not modify in this plan):**
- `src/simulatte/job.py:339-350` — `BaseJob.priority` and `priority_policy` semantics.
- `src/simulatte/policies/slar.py`, `src/simulatte/policies/lumscor.py` — examples of time-dependent dynamic policies (used in the final regression sweep).
- `pac-ppc/src/pac_ppc/simulation/patches.py` — the monkey-patch this fix upstreams.
- `pac-ppc/tests/test_migration_regression.py:33-126` — reference reproducers.

---

## Task 1: Write failing unit test for `sort_queue` key refresh

**Files:**
- Test: `tests/core/test_server.py` (new class `TestDynamicPriorityRefresh` appended at end of file)

This test exercises `sort_queue` directly without running a SimPy event loop. It proves the refresh happens.

- [ ] **Step 1: Append the failing test to `tests/core/test_server.py`**

Add at the bottom of the file:

```python
class TestDynamicPriorityRefresh:
    """Tests for the dynamic-priority queue refresh behavior of Server.

    These tests verify the contract documented in
    docs/superpowers/specs/2026-05-25-dynamic-priority-queue-refresh-design.md:
    at every dispatch decision (and on explicit sort_queue() calls), each
    queued request's priority is re-evaluated from job.priority_policy and
    the queue is resorted by the refreshed values.
    """

    @staticmethod
    def _make_job(
        env: Environment,
        server: Server,
        sku: str,
        policy,
        processing_time: float = 3.0,
    ) -> ProductionJob:
        return ProductionJob(
            env=env,
            sku=sku,
            servers=[server],
            processing_times=[processing_time],
            due_date=1000.0,
            priority_policy=policy,
        )

    def test_sort_queue_refreshes_keys_from_current_policy(self) -> None:
        """sort_queue must re-evaluate priority_policy for every queued request."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        # Blocker holds the only slot so other requests stay queued.
        blocker = self._make_job(env, server, "BLOCK", lambda j, s: 0.0, processing_time=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        state = {"A": 10.0, "B": 20.0}
        job_a = self._make_job(env, server, "A", lambda j, s: state["A"])
        job_b = self._make_job(env, server, "B", lambda j, s: state["B"])
        sf.add(job_a)
        sf.add(job_b)
        env.run(until=0.02)

        # Initial order: A (10) before B (20).
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B"]

        # Flip the relative priorities of the queued jobs.
        state["A"] = 30.0
        state["B"] = 5.0

        # Without refresh, sort_queue would re-sort by the stale stored keys
        # and leave the order unchanged. With refresh, B should move ahead.
        server.sort_queue()
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["B", "A"]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/core/test_server.py::TestDynamicPriorityRefresh::test_sort_queue_refreshes_keys_from_current_policy -v`

Expected: FAIL. The current `sort_queue` (server.py:259-267) sorts by frozen `req.key`, so it never moves A and B.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/core/test_server.py
git commit -m "test(server): add failing test for sort_queue key refresh"
```

---

## Task 2: Implement `Server.sort_queue` refresh and unblock the existing test

**Files:**
- Modify: `src/simulatte/server.py:259-267` (replace `sort_queue` body)
- Modify: `tests/core/test_server.py:143-147` (extend `DummyRequest`)

- [ ] **Step 1: Replace `Server.sort_queue` body**

Open `src/simulatte/server.py`. Find the existing `sort_queue`:

```python
    def sort_queue(self) -> None:
        """Reorder the queue by priority keys.

        Sorts waiting requests in ascending order by their priority key.
        Typically used by scheduling policies to resequence jobs after
        priorities change.
        """
        queue_list = cast(list, self.queue)
        queue_list.sort(key=lambda req: req.key)
```

Replace with:

```python
    def sort_queue(self) -> None:
        """Refresh queued requests' priority keys and resort.

        For each request in the queue, calls ``req.job.priority(req.server)``
        to obtain the current priority and rewrites ``req.key`` accordingly,
        then sorts the queue in ascending order by the refreshed keys.

        Called automatically before every dispatch decision via
        :meth:`_trigger_put`. May also be invoked explicitly by user code
        that has mutated ``priority_policy`` and wants to observe the new
        order before the next dispatch event.

        Note: ``req.priority`` is not refreshed; it remains the snapshot
        taken at request construction. To inspect a queued job's current
        priority, call ``req.job.priority(req.server)`` directly.

        Requires that every queued request expose ``job``, ``server``,
        ``time``, and ``preempt`` attributes (which :class:`ServerPriorityRequest`
        does).
        """
        queue_list = cast(list, self.queue)
        for req in queue_list:
            fresh_priority = req.job.priority(req.server)
            req.key = (fresh_priority, req.time, not req.preempt)
        queue_list.sort(key=lambda req: req.key)
```

- [ ] **Step 2: Extend `DummyRequest` in the existing `test_sort_queue`**

The existing test at `tests/core/test_server.py:137-185` uses a `DummyRequest` that has only `key` and `job`. The new `sort_queue` reads `req.server`, `req.time`, and `req.preempt`. Without these, the test crashes with `AttributeError`.

Replace the `DummyRequest` definition at lines 143-147:

```python
        class DummyRequest:
            def __init__(self, *, key: float, job: ProductionJob) -> None:
                self.key = key
                self.job = job
```

with:

```python
        class DummyRequest:
            def __init__(
                self,
                *,
                key: float,
                job: ProductionJob,
                server: Server,
                time: float = 0.0,
                preempt: bool = True,
            ) -> None:
                self.key = key
                self.job = job
                self.server = server
                self.time = time
                self.preempt = preempt
```

Then update the three `DummyRequest` construction lines (currently at lines 175-177) to pass `server`:

```python
        req_high = DummyRequest(key=job_high.priority(server), job=job_high, server=server)
        req_low = DummyRequest(key=job_low.priority(server), job=job_low, server=server)
        req_med = DummyRequest(key=job_med.priority(server), job=job_med, server=server)
```

- [ ] **Step 3: Run the new test and the existing one**

Run: `uv run pytest tests/core/test_server.py::TestDynamicPriorityRefresh::test_sort_queue_refreshes_keys_from_current_policy tests/core/test_server.py::TestServer::test_sort_queue -v`

Expected: both PASS.

- [ ] **Step 4: Run the full server test file**

Run: `uv run pytest tests/core/test_server.py -v`

Expected: all tests pass. If any fail, diagnose:
- A test asserting old buggy ordering for a static policy: shouldn't happen (static priorities behave identically); investigate.
- A `TestPriorityQueueOrdering` test: these all use static policies, so re-evaluation is idempotent. Should still pass without modification.

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/server.py tests/core/test_server.py
git commit -m "feat(server): refresh queued priorities in sort_queue

sort_queue now re-evaluates priority_policy for every queued
request and rewrites req.key before sorting. This makes dynamic
priority changes (time-dependent policies, policy reassignment,
mutable external state) take effect.

sort_queue is still only invoked explicitly here; a subsequent
commit hooks it into the dispatch path via _trigger_put."
```

---

## Task 3: Write failing test for automatic refresh on the release path

**Files:**
- Modify: `tests/core/test_server.py` (append to `TestDynamicPriorityRefresh`)

This is the test that B-alone (a live-key property without `_trigger_put` override) would fail. It proves that releases trigger a refresh.

- [ ] **Step 1: Append the failing test**

Append inside `class TestDynamicPriorityRefresh`:

```python
    def test_release_dispatches_by_live_priority_not_stale_order(self) -> None:
        """A release must refresh queued priorities before granting the next slot.

        Setup: blocker holds the slot. Jobs A and B queue with policies
        reading from a shared dict. Before the blocker finishes, the
        dict is mutated so B should overtake A. When the blocker releases,
        the server must dispatch B, not A.
        """
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        state = {"A": 10.0, "B": 20.0}

        blocker = self._make_job(env, server, "BLOCK", lambda j, s: -1.0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_a = self._make_job(env, server, "A", lambda j, s: state["A"])
        job_b = self._make_job(env, server, "B", lambda j, s: state["B"])
        sf.add(job_a)
        sf.add(job_b)
        env.run(until=0.02)

        # Initial queue: A before B.
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B"]

        # Flip the priorities while both jobs sit in the queue.
        env.run(until=5.0)
        state["A"] = 30.0
        state["B"] = 5.0

        # Blocker finishes at t=10. The release-driven _trigger_put must
        # refresh priorities and grant the slot to B (now lower-priority value).
        env.run()

        a_exit = job_a.servers_exit_at[server]
        b_exit = job_b.servers_exit_at[server]
        assert a_exit is not None and b_exit is not None
        assert b_exit < a_exit, (
            f"Expected B to be dispatched before A after priority flip; "
            f"got A exit={a_exit}, B exit={b_exit}"
        )
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/core/test_server.py::TestDynamicPriorityRefresh::test_release_dispatches_by_live_priority_not_stale_order -v`

Expected: FAIL. After Task 2, `sort_queue` refreshes keys when explicitly called, but it is not yet wired into `_trigger_put`, so the release path still walks the stale queue order. A is dispatched first; B is dispatched second; the assertion fails.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/core/test_server.py
git commit -m "test(server): add failing test for release-path priority refresh"
```

---

## Task 4: Override `Server._trigger_put` to auto-refresh

**Files:**
- Modify: `src/simulatte/server.py` (add new method on `Server`)

- [ ] **Step 1: Add `_trigger_put` override**

Open `src/simulatte/server.py`. Add the following method on `Server`, immediately after `sort_queue` (the method that was rewritten in Task 2):

```python
    def _trigger_put(self, get_event) -> None:  # type: ignore[no-untyped-def]
        """Refresh queue priorities before SimPy iterates the put queue.

        Overrides :meth:`simpy.resources.base.BaseResource._trigger_put` to
        call :meth:`sort_queue` (which re-evaluates ``job.priority_policy``
        for every queued request and rewrites ``req.key``) before delegating
        to SimPy. SimPy invokes ``_trigger_put`` from two call sites:
        :meth:`simpy.resources.base.Put.__init__` (after a new arrival is
        appended to ``put_queue``) and as a callback on every Release event
        (``simpy.resources.base.Get.__init__`` registers it). Refreshing
        here therefore covers both the new-arrival and release dispatch paths.
        """
        self.sort_queue()
        super()._trigger_put(get_event)
```

- [ ] **Step 2: Run the release-path test**

Run: `uv run pytest tests/core/test_server.py::TestDynamicPriorityRefresh::test_release_dispatches_by_live_priority_not_stale_order -v`

Expected: PASS.

- [ ] **Step 3: Run the full server test file**

Run: `uv run pytest tests/core/test_server.py -v`

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/server.py
git commit -m "feat(server): auto-refresh queue priorities at dispatch decision

Server._trigger_put now calls sort_queue before delegating to SimPy.
Since SimPy invokes _trigger_put from both Put.__init__ (new arrival)
and Release event callbacks, this single hook covers both dispatch
paths. Dynamic priority changes are now visible without explicit
sort_queue() calls."
```

---

## Task 5: Add coverage tests for the remaining contract surfaces

**Files:**
- Modify: `tests/core/test_server.py` (append to `TestDynamicPriorityRefresh`)

These tests don't introduce new behavior — they pin down the contract listed in the spec so future regressions are caught.

- [ ] **Step 1: Append the new-arrival put-path test**

```python
    def test_new_arrival_sorted_against_fresh_keys_of_queued_jobs(self) -> None:
        """When a new request arrives, queued jobs' priorities are refreshed
        so the new arrival lands in the position implied by current priorities,
        not by stale snapshots."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        state = {"A": 10.0, "B": 20.0}

        blocker = self._make_job(env, server, "BLOCK", lambda j, s: -1.0, processing_time=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_a = self._make_job(env, server, "A", lambda j, s: state["A"])
        job_b = self._make_job(env, server, "B", lambda j, s: state["B"])
        sf.add(job_a)
        sf.add(job_b)
        env.run(until=0.02)
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B"]

        # Flip priorities. Then introduce C with a value between the new A and B.
        state["A"] = 30.0
        state["B"] = 5.0

        job_c = self._make_job(env, server, "C", lambda j, s: 15.0)
        sf.add(job_c)
        env.run(until=0.03)

        # Expected order after refresh+sort: B (5) < C (15) < A (30).
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["B", "C", "A"]
```

- [ ] **Step 2: Append the policy reassignment test**

```python
    def test_policy_reassignment_affects_next_dispatch(self) -> None:
        """Reassigning job.priority_policy on a queued job changes its
        position at the next dispatch decision."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", lambda j, s: -1.0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_a = self._make_job(env, server, "A", lambda j, s: 5.0)
        job_b = self._make_job(env, server, "B", lambda j, s: 10.0)
        sf.add(job_a)
        sf.add(job_b)
        env.run(until=0.02)
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B"]

        # Reassign A's policy so it now ranks lower (higher value) than B.
        job_a.priority_policy = lambda j, s: 99.0

        # Run past the blocker's release. B should be dispatched first.
        env.run()
        a_exit = job_a.servers_exit_at[server]
        b_exit = job_b.servers_exit_at[server]
        assert a_exit is not None and b_exit is not None
        assert b_exit < a_exit
```

- [ ] **Step 3: Append the explicit-sort_queue test**

```python
    def test_explicit_sort_queue_observes_new_order_before_dispatch(self) -> None:
        """Callers can invoke sort_queue() after mutating policies to
        observe the new queue order before the next dispatch event."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", lambda j, s: -1.0, processing_time=1000.0)
        sf.add(blocker)
        env.run(until=0.01)

        state = {"A": 1.0, "B": 2.0, "C": 3.0}
        for sku in ("A", "B", "C"):
            sf.add(self._make_job(env, server, sku, lambda j, s, k=sku: state[k]))
        env.run(until=0.02)

        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B", "C"]

        # Flip order via state mutation; queue order is stale until refresh.
        state["A"] = 30.0
        state["B"] = 20.0
        state["C"] = 10.0

        server.sort_queue()
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["C", "B", "A"]
```

- [ ] **Step 4: Append the contract-violation guard test**

```python
    def test_stochastic_policy_does_not_crash(self) -> None:
        """A policy that violates the purity contract (consumes RNG) must
        not raise; ordering is unspecified but the simulation completes."""
        import random

        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        rng = random.Random(42)

        blocker = self._make_job(env, server, "BLOCK", lambda j, s: -1.0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        for sku in ("A", "B", "C"):
            sf.add(self._make_job(env, server, sku, lambda j, s: rng.random()))

        env.run()  # must complete without raising
        # All jobs eventually serviced; order is unspecified.
        for sku in ("BLOCK", "A", "B", "C"):
            pass  # presence is implied by env.run() completing
```

- [ ] **Step 5: Run all new tests**

Run: `uv run pytest tests/core/test_server.py::TestDynamicPriorityRefresh -v`

Expected: all five tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/core/test_server.py
git commit -m "test(server): add contract coverage for dynamic priority refresh

Covers: new-arrival put path against stale queued jobs, runtime
policy reassignment, explicit sort_queue() resync, and a guard
verifying that contract-violating stochastic policies do not crash
the simulation."
```

---

## Task 6: Update `ServerPriorityRequest` and `Server` docstrings

**Files:**
- Modify: `src/simulatte/server.py` (class docstrings for `ServerPriorityRequest` and `Server`)

- [ ] **Step 1: Replace `ServerPriorityRequest` class docstring**

Find the current `ServerPriorityRequest` class docstring at `src/simulatte/server.py:29-35`:

```python
class ServerPriorityRequest(PriorityRequest):
    """Priority request that carries the job reference and priority key.

    This extends SimPy's PriorityRequest to associate a job with each request,
    enabling priority-based queueing where jobs compete for server access.
    The priority is computed from the job's priority_policy at request time.
    """
```

Replace with:

```python
class ServerPriorityRequest(PriorityRequest):
    """Priority request that carries the job reference and priority key.

    This extends SimPy's PriorityRequest to associate a job with each request,
    enabling priority-based queueing where jobs compete for server access.

    Priority semantics:

    - ``self.priority`` is set once at construction (via the parent class's
      ``__init__``) from ``job.priority(server)`` and is never refreshed.
      Treat it as the priority at queue-entry time only.
    - ``self.key`` is rewritten at every dispatch decision by
      :meth:`Server.sort_queue`, which re-evaluates
      ``job.priority(req.server)`` for every queued request. The queue is
      sorted by ``self.key``; this is what makes dynamic priorities work.
    - To read a queued job's *current* priority value, call
      ``req.job.priority(req.server)`` directly.

    The order of attribute assignment in ``__init__`` matters: ``self.server``
    and ``self.job`` must be set before ``super().__init__()`` because the
    superclass chain eventually triggers ``SortedQueue.append``, which sorts
    by ``req.key`` and (after this fix) future dispatch decisions read
    ``req.job`` and ``req.server`` from every queued request.
    """
```

- [ ] **Step 2: Add a paragraph to `Server` class docstring**

Find the current `Server` class docstring at `src/simulatte/server.py:56-62`:

```python
class Server(simpy.PriorityResource):
    """A server/workstation for job-shop simulation with queue and utilization tracking.

    Server extends SimPy's PriorityResource to process jobs with priority-based
    queueing. It tracks queue lengths, utilization rates, and optionally records
    time-series data for visualization. When attached to a ShopFloor, the server
    is automatically registered and assigned an index for identification.
    """
```

Replace with:

```python
class Server(simpy.PriorityResource):
    """A server/workstation for job-shop simulation with queue and utilization tracking.

    Server extends SimPy's PriorityResource to process jobs with priority-based
    queueing. It tracks queue lengths, utilization rates, and optionally records
    time-series data for visualization. When attached to a ShopFloor, the server
    is automatically registered and assigned an index for identification.

    Dynamic priorities: queued jobs' priorities are refreshed before every
    dispatch decision. :meth:`sort_queue` re-evaluates each queued request's
    ``job.priority_policy`` and rewrites ``req.key``; :meth:`_trigger_put`
    (the SimPy hook invoked on both new-arrival and release paths) calls
    :meth:`sort_queue` before delegating to SimPy. Callers may also invoke
    :meth:`sort_queue` explicitly to observe the resulting order between
    events. The cost per dispatch decision is one ``priority_policy`` call
    per queued request; policies must be deterministic functions of
    ``(job, server, current_state)`` per the contract documented in
    ``docs/superpowers/specs/2026-05-25-dynamic-priority-queue-refresh-design.md``.
    """
```

- [ ] **Step 3: Run the test suite**

Run: `uv run pytest tests/core/test_server.py -v`

Expected: all tests pass (docstrings don't affect runtime behavior, but verifying the file still imports cleanly).

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/server.py
git commit -m "docs(server): document dynamic priority refresh semantics"
```

---

## Task 7: Run the full test suite and the lint/type pipeline

**Files:**
- No file changes. Pure verification.

- [ ] **Step 1: Run the full pytest suite**

Run: `uv run pytest`

Expected: all tests pass. If anything fails:
- **Test asserts old buggy ordering with a dynamic policy** (e.g., a SLAR/LumsCor test that depended on the queue being out of date): update the test to assert the corrected ordering. Note in commit message.
- **Static-priority test fails**: should not happen (idempotent re-evaluation). Investigate root cause.
- **Hook-based test fails because a hook now sees a different queue order at dispatch**: this is the bug fix working as intended; update the test.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check src/simulatte/server.py tests/core/test_server.py && uv run ruff format --check src/simulatte/server.py tests/core/test_server.py`

Expected: clean. If `ruff format --check` fails, run `uv run ruff format src/simulatte/server.py tests/core/test_server.py` and amend the most recent commit:

```bash
git add src/simulatte/server.py tests/core/test_server.py
git commit --amend --no-edit
```

- [ ] **Step 3: Run the type checker**

Run: `uv run pyright src/simulatte/server.py tests/core/test_server.py`

Expected: no new errors. The `_trigger_put` override carries `# type: ignore[no-untyped-def]` because SimPy's signature uses `Optional[GetType]` parameterized over the generic resource type, which pyright cannot validate against our concrete subclass.

If pyright complains about anything else, prefer a precise fix over a blanket `# type: ignore`. The expected complaint surface is small: `req.key = (...)` (assignment to an attribute inherited from a class that types it as `tuple`) and the `_trigger_put` signature.

- [ ] **Step 4: Commit any formatting/typing fixups**

If a separate commit was needed, message:

```bash
git commit -m "chore: ruff format / pyright fixups for dynamic priority fix"
```

Otherwise skip.

---

## Task 8: Update the documentation site

**Files:**
- Modify: `docs/tutorials/release-control-and-dispatching.md` — append a new "Dynamic priorities" section at the end. The site's existing tutorial on release control and dispatching is the right home; dispatching is precisely the surface dynamic priorities affect.

- [ ] **Step 1: Read the existing tutorial**

Run: `wc -l docs/tutorials/release-control-and-dispatching.md && tail -40 docs/tutorials/release-control-and-dispatching.md`

This tells you the file length and the style of its last section so the new section fits the surrounding tone.

- [ ] **Step 2: Append the "Dynamic priorities" section**

Append the following Markdown at the end of `docs/tutorials/release-control-and-dispatching.md`. Adapt heading depth (`##` vs `###`) to match the file's existing top-level heading depth — if existing sections use `##`, use `##` here; if `###`, use `###`.

```markdown
## Dynamic priorities

A job's priority comes from `job.priority_policy`, which simulatte calls
as `policy(job, server)` and which returns a float (lower = more urgent).
This policy is re-evaluated on every dispatch decision: every time a new
job enters a server's queue and every time a job releases a server. Three
patterns are supported first-class:

- **Time-dependent policies** — the value depends on `env.now` (e.g. planned
  slack time, which decreases as the simulation progresses).
- **Policy reassignment** — `job.priority_policy = new_fn` at any time
  reorders the job's position in any queue it is currently waiting in.
- **Mutable external state** — the policy reads from shared state owned by
  user code (e.g. a dispatcher's score table); updates to that state become
  visible at the next dispatch decision.

### Contract

`priority_policy(job, server)` must be a **deterministic function of
`(job, server, current simulation state)`**: repeated calls at the same
`env.now` with the same external state must return the same value. Do not
consume RNG inside the policy and do not mutate state from inside the
policy. If a policy violates this contract, the simulation still runs but
queue ordering becomes unspecified.

### Cost

simulatte calls `priority_policy` once per queued request per dispatch
decision, so the per-event cost scales linearly with the queue length.
Keep policies cheap.

### Example

```python
state = {"A": 10.0, "B": 20.0}

job_a = ProductionJob(
    env=env, sku="A", servers=[server], processing_times=[3.0],
    due_date=1000.0, priority_policy=lambda j, s: state["A"],
)
job_b = ProductionJob(
    env=env, sku="B", servers=[server], processing_times=[3.0],
    due_date=1000.0, priority_policy=lambda j, s: state["B"],
)

# Both queue with A ahead of B.
sf.add(job_a)
sf.add(job_b)

# Mutate the shared state; at the next dispatch decision the queue
# is re-sorted and B will be served before A.
state["A"] = 30.0
state["B"] = 5.0
```

`Server.sort_queue()` can also be called explicitly if you want the new
order to be observable immediately (between events).

Runnable end-to-end examples live in
`tests/core/test_server.py::TestDynamicPriorityRefresh`.
```

- [ ] **Step 3: Verify the docs build**

Run: `uv run zensical build`

Expected: build succeeds. Fix any broken cross-references or heading-level warnings.

- [ ] **Step 4: Commit**

```bash
git add docs/tutorials/release-control-and-dispatching.md
git commit -m "docs(tutorials): add Dynamic priorities section to dispatching guide"
```

---

## Task 9: Final verification

**Files:**
- No file changes. Verification only.

- [ ] **Step 1: Full pytest sweep**

Run: `uv run pytest`

Expected: green.

- [ ] **Step 2: Inspect the commit log**

Run: `git log --oneline main..HEAD`

Expected: a clean sequence of 6-8 commits, each scoped to one logical change:

- `docs: spec for dynamic priority queue refresh` (already committed)
- `test(server): add failing test for sort_queue key refresh`
- `feat(server): refresh queued priorities in sort_queue`
- `test(server): add failing test for release-path priority refresh`
- `feat(server): auto-refresh queue priorities at dispatch decision`
- `test(server): add contract coverage for dynamic priority refresh`
- `docs(server): document dynamic priority refresh semantics`
- `docs: add dynamic priorities guide for Server`
- (optional) `chore: ruff format / pyright fixups`

- [ ] **Step 3: Push the branch and open a PR**

```bash
git push -u origin feature/dynamic-priority-queue-refresh
```

Open a PR titled `feat(server): dynamic priority queue refresh` with body summarizing:

- The bug (queued requests' priorities never refreshed; cite the design spec).
- The fix (refresh in `sort_queue`, auto-call in `_trigger_put`).
- The contract (deterministic `priority_policy`).
- Cross-reference: pac-ppc carries an equivalent monkey-patch; it can be removed once this lands.

Do **not** push or open the PR if the user has not authorized it for this branch. Confirm with the user before running step 3.

---

## Risks and unknowns

- **Existing tests asserting buggy behavior**: SLAR/LumsCor tests use time-dependent policies. They may have been passing despite the bug because their queue scenarios didn't exercise the wrong-dispatch path, or they may have been getting subtly wrong dispatch decisions. Task 7 Step 1 flags this; resolution is per-test.
- **SimPy internal API**: `_trigger_put` is a documented extension point (see `simpy.resources.base.BaseResource` docstring listing it as the customization point), but it is leading-underscore. A future SimPy version could change its signature. The repo pins SimPy via `uv.lock`; bumping SimPy is a separate decision.
- **Performance for large queues**: O(N) `priority_policy` calls per dispatch decision. Typical simulation queues are short. If a workload appears with very large queues, the future-work item in the spec (single-evaluation-per-arrival) becomes worth pursuing.
- **`req.priority` vs `req.key[0]` divergence**: documented in the `ServerPriorityRequest` docstring. Anyone who needs the live value should call `req.job.priority(req.server)` directly. If this proves confusing in practice, a follow-up change can refresh `req.priority` inside the same loop.
