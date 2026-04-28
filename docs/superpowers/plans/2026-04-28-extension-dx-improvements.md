# Extension DX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve simulatte's extension APIs so common patterns (post-construction hook registration, plain sync callbacks, event subscription, dispatcher wiring) are idiomatic and don't require accessing private internals.

**Architecture:** Changes are additive to internal storage (`_before_operation`, `_after_operation`, `_on_job_finished` lists) — we add public registration methods that append to them, loosen the `OperationHook` protocol to allow both sync and generator hooks, and add object-level convenience methods on `PreShopPool` and `Server`. A `Dispatcher` Protocol + `attach_dispatcher()` provides one-call wiring for objects that implement multiple hooks.

**Tech Stack:** Python 3.12+, SimPy, pytest

---

### Task 1: Server `is_idle` property and `current_jobs` property

**Files:**
- Modify: `src/simulatte/server.py:106-109` (add after `empty` property)
- Test: `tests/core/test_server.py`

- [ ] **Step 1: Write failing tests for `is_idle`**

Add to `TestServer` class in `tests/core/test_server.py`:

```python
def test_is_idle_no_jobs(self) -> None:
    """is_idle should be True when server has no users and no queue."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    assert server.is_idle

def test_is_idle_while_processing(self) -> None:
    """is_idle should be False when server is processing a job."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
    sf.add(job)
    env.run(until=0.1)

    assert not server.is_idle

def test_is_idle_with_queue(self) -> None:
    """is_idle should be False when jobs are queued."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100], due_date=200)
    sf.add(job1)
    sf.add(job2)
    env.run(until=0.1)

    assert not server.is_idle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_server.py::TestServer::test_is_idle_no_jobs tests/core/test_server.py::TestServer::test_is_idle_while_processing tests/core/test_server.py::TestServer::test_is_idle_with_queue -v`
Expected: FAIL with `AttributeError: 'Server' object has no attribute 'is_idle'`

- [ ] **Step 3: Write failing tests for `current_jobs`**

Add to `TestServer` class in `tests/core/test_server.py`:

```python
def test_current_jobs_empty(self) -> None:
    """current_jobs should be empty tuple when no jobs are processing."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    assert server.current_jobs == ()

def test_current_jobs_while_processing(self) -> None:
    """current_jobs should contain the job being processed."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
    sf.add(job)
    env.run(until=0.1)

    assert server.current_jobs == (job,)

def test_current_jobs_parallel_capacity(self) -> None:
    """current_jobs should contain all jobs being processed in parallel."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=2, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100], due_date=200)
    sf.add(job1)
    sf.add(job2)
    env.run(until=0.1)

    assert set(server.current_jobs) == {job1, job2}
```

- [ ] **Step 4: Run new tests to verify they fail**

Run: `uv run pytest tests/core/test_server.py::TestServer::test_current_jobs_empty tests/core/test_server.py::TestServer::test_current_jobs_while_processing tests/core/test_server.py::TestServer::test_current_jobs_parallel_capacity -v`
Expected: FAIL with `AttributeError: 'Server' object has no attribute 'current_jobs'`

- [ ] **Step 5: Implement `is_idle` and `current_jobs` properties**

In `src/simulatte/server.py`, add after the `empty` property (after line 109):

```python
@property
def is_idle(self) -> bool:
    """Whether the server has no active users and an empty queue."""
    return self.count == 0 and self.empty

@property
def current_jobs(self) -> tuple[BaseJob, ...]:
    """Jobs currently occupying active server slots (includes hook/material phases)."""
    return tuple(request.job for request in self.users)
```

- [ ] **Step 6: Run all server tests to verify they pass**

Run: `uv run pytest tests/core/test_server.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/server.py tests/core/test_server.py
git commit -m "feat(server): add is_idle and current_jobs properties"
```

---

### Task 2: Use `is_idle` in starvation avoidance

**Files:**
- Modify: `src/simulatte/policies/starvation_avoidance.py:47-54`
- Test: existing `tests/core/test_starvation_avoidance.py`

- [ ] **Step 1: Refactor starvation avoidance to use `is_idle`**

In `src/simulatte/policies/starvation_avoidance.py`, replace lines 47-54:

```python
    while True:
        new_job_in_psp: ProductionJob = yield psp.new_job
        first_server: Server = new_job_in_psp.servers[0]
        is_queue_empty: bool = first_server.empty
        is_user_empty: bool = len(first_server.users) == 0
        if is_queue_empty and is_user_empty:
            psp.remove(job=new_job_in_psp)
            shopfloor.add(new_job_in_psp)
```

with:

```python
    while True:
        new_job_in_psp: ProductionJob = yield psp.new_job
        first_server: Server = new_job_in_psp.servers[0]
        if first_server.is_idle:
            psp.remove(job=new_job_in_psp)
            shopfloor.add(new_job_in_psp)
```

Also remove the now-unused `Server` import at line 13 (`from simulatte.server import Server`) — wait, `Server` is still used for the type annotation of `first_server` at runtime. Actually, `first_server` type comes from `new_job_in_psp.servers[0]` which returns `Server` — the local variable type annotation `first_server: Server` can stay or go. Let's keep it since it's used as a type annotation. Actually looking again, `Server` is imported at line 13 as a runtime import. Check whether it's used elsewhere in this module — it's only used for the local annotation `first_server: Server` which is now on a single line. Keep the import; removing type annotations is not the goal here.

- [ ] **Step 2: Run starvation avoidance tests**

Run: `uv run pytest tests/core/test_starvation_avoidance.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/simulatte/policies/starvation_avoidance.py
git commit -m "refactor(starvation_avoidance): use server.is_idle"
```

---

### Task 3: Loosen `OperationHook` protocol for sync hooks

**Files:**
- Modify: `src/simulatte/shopfloor.py:24` (imports), `src/simulatte/shopfloor.py:44-79` (protocol), `src/simulatte/shopfloor.py:908-909` (before-hook call site), `src/simulatte/shopfloor.py:926-927` (after-hook call site)
- Test: `tests/core/test_shopfloor.py`

- [ ] **Step 1: Write failing test for sync before-operation hook**

Add to `tests/core/test_shopfloor.py`:

```python
def test_sync_before_operation_hook() -> None:
    """A plain sync function (no yield) should work as a before_operation hook."""
    hook_calls: list[float] = []

    def sync_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> None:
        hook_calls.append(server.env.now)

    env = Environment()
    sf = ShopFloor(env=env, before_operation=sync_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1
    assert hook_calls[0] == 0.0
    assert job.finished_at == pytest.approx(5.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_shopfloor.py::test_sync_before_operation_hook -v`
Expected: FAIL (TypeError from `yield from None`)

- [ ] **Step 3: Write failing test for mixed sync + generator hooks**

Add to `tests/core/test_shopfloor.py`:

```python
def test_mixed_sync_and_generator_hooks_execute_in_order() -> None:
    """Sync and generator hooks in the same list execute in registration order."""
    from simulatte.typing import ProcessGenerator

    execution_order: list[str] = []

    def sync_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> None:
        execution_order.append("sync")

    def gen_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("gen")
        yield server.env.timeout(0.1)

    env = Environment()
    sf = ShopFloor(env=env, before_operation=[sync_hook, gen_hook])
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert execution_order == ["sync", "gen"]
    assert job.finished_at == pytest.approx(5.1)
```

- [ ] **Step 4: Write failing test for sync after-operation hook**

Add to `tests/core/test_shopfloor.py`:

```python
def test_sync_after_operation_hook() -> None:
    """A plain sync function should work as an after_operation hook."""
    hook_calls: list[float] = []

    def sync_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> None:
        hook_calls.append(server.env.now)

    env = Environment()
    sf = ShopFloor(env=env, after_operation=sync_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1
    assert hook_calls[0] == pytest.approx(5.0)
```

- [ ] **Step 5: Implement sync hook support**

In `src/simulatte/shopfloor.py`:

1. Add `import inspect` to the imports section (after `from __future__ import annotations`, before `from collections.abc import ...`):

```python
import inspect
```

2. Update the `OperationHook` protocol return type (line 68). Change:

```python
    ) -> ProcessGenerator:
```

to:

```python
    ) -> ProcessGenerator | None:
```

3. Update the protocol docstring (lines 46-59) to show both sync and generator examples:

```python
    """Hook called before or after each operation.

    Hooks may be plain synchronous functions (returning None) or
    generator-based (yielding SimPy events). Both styles can coexist
    in the same hook list and execute in registration order.

    Examples:
        A synchronous dispatch hook::

            def dispatch_hook(job, server, op_index, processing_time):
                server.sort_queue()

        A generator hook that adds setup time::

            def setup_time_hook(job, server, op_index, processing_time):
                setup = 2.0 if job.sku.startswith("COMPLEX") else 0.5
                yield server.env.timeout(setup)

            shopfloor = ShopFloor(env=env, on_before_operation=setup_time_hook)
    """
```

4. Update the `__call__` docstring to reflect the return type (lines 69-79):

```python
        """Execute the hook.

        Args:
            job: The job being processed.
            server: The server where the operation occurs.
            op_index: Zero-based index of the current operation.
            processing_time: Duration of the operation.

        Returns:
            None for synchronous hooks, or a generator yielding SimPy events.
        """
```

5. Update the before-operation hook invocation in `main()` (lines 907-909). Change:

```python
                # Before-operation hooks
                for hook in self._before_operation:
                    yield from hook(job, server, op_index, processing_time)
```

to:

```python
                # Before-operation hooks
                for hook in self._before_operation:
                    result = hook(job, server, op_index, processing_time)
                    if result is None:
                        continue
                    if inspect.isgenerator(result):
                        yield from result
                    else:
                        raise TypeError(
                            f"OperationHook must return None or a generator, got {type(result).__name__}"
                        )
```

6. Update the after-operation hook invocation in `main()` (lines 925-927). Change:

```python
                # After-operation hooks
                for hook in self._after_operation:
                    yield from hook(job, server, op_index, processing_time)
```

to:

```python
                # After-operation hooks
                for hook in self._after_operation:
                    result = hook(job, server, op_index, processing_time)
                    if result is None:
                        continue
                    if inspect.isgenerator(result):
                        yield from result
                    else:
                        raise TypeError(
                            f"OperationHook must return None or a generator, got {type(result).__name__}"
                        )
```

- [ ] **Step 6: Run all shopfloor tests**

Run: `uv run pytest tests/core/test_shopfloor.py -v`
Expected: All PASS (new sync tests pass, existing generator tests still pass)

- [ ] **Step 7: Commit**

```bash
git add src/simulatte/shopfloor.py tests/core/test_shopfloor.py
git commit -m "feat(shopfloor): support sync callbacks in OperationHook protocol"
```

---

### Task 4: Rename init kwargs to `on_before_operation`, `on_after_operation`

**Files:**
- Modify: `src/simulatte/shopfloor.py:644-683` (init signature + body)
- Modify: `tests/core/test_shopfloor.py` (4 call sites)

- [ ] **Step 1: Rename init kwargs in ShopFloor**

In `src/simulatte/shopfloor.py`, change line 644:

```python
        before_operation: OperationHook | Sequence[OperationHook] | None = None,
```

to:

```python
        on_before_operation: OperationHook | Sequence[OperationHook] | None = None,
```

Change line 645:

```python
        after_operation: OperationHook | Sequence[OperationHook] | None = None,
```

to:

```python
        on_after_operation: OperationHook | Sequence[OperationHook] | None = None,
```

Update the docstring entries (lines 670-673):

```python
            on_before_operation: Hook(s) called after acquiring server but before
                material delivery and processing. Can be a single hook or list.
            on_after_operation: Hook(s) called after processing completes but
                before signaling. Can be a single hook or list.
```

Update the normalization calls (lines 681-682):

```python
        self._before_operation: list[OperationHook] = self._normalize_hooks(on_before_operation)
        self._after_operation: list[OperationHook] = self._normalize_hooks(on_after_operation)
```

- [ ] **Step 2: Update test call sites**

In `tests/core/test_shopfloor.py`, update 4 call sites:

Line 257 — change `before_operation=setup_hook` to `on_before_operation=setup_hook`

Line 286 — change `after_operation=after_hook` to `on_after_operation=after_hook`

Line 318 — change `before_operation=hooks` to `on_before_operation=hooks`

Line 450 — change `before_operation=track_hook` to `on_before_operation=track_hook`

Also update the 3 new tests from Task 3:

`test_sync_before_operation_hook` — change `before_operation=sync_hook` to `on_before_operation=sync_hook`

`test_mixed_sync_and_generator_hooks_execute_in_order` — change `before_operation=[sync_hook, gen_hook]` to `on_before_operation=[sync_hook, gen_hook]`

`test_sync_after_operation_hook` — change `after_operation=sync_hook` to `on_after_operation=sync_hook`

- [ ] **Step 3: Run all shopfloor tests**

Run: `uv run pytest tests/core/test_shopfloor.py -v`
Expected: All PASS

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS (builders use `ShopFloor()` without hook kwargs, so unaffected)

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/shopfloor.py tests/core/test_shopfloor.py
git commit -m "refactor(shopfloor): rename init kwargs to on_before_operation, on_after_operation"
```

---

### Task 5: Post-init hook registration methods on ShopFloor

**Files:**
- Modify: `src/simulatte/shopfloor.py` (add methods after `_normalize_callbacks`, ~line 738)
- Test: `tests/core/test_shopfloor.py`

- [ ] **Step 1: Write failing tests for post-init registration**

Add to `tests/core/test_shopfloor.py`:

```python
def test_on_before_operation_post_init() -> None:
    """on_before_operation() should register a hook after construction."""
    hook_calls: list[float] = []

    def hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> None:
        hook_calls.append(server.env.now)

    env = Environment()
    sf = ShopFloor(env=env)
    sf.on_before_operation(hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1


def test_on_after_operation_post_init() -> None:
    """on_after_operation() should register a hook after construction."""
    hook_calls: list[float] = []

    def hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> None:
        hook_calls.append(server.env.now)

    env = Environment()
    sf = ShopFloor(env=env)
    sf.on_after_operation(hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1
    assert hook_calls[0] == pytest.approx(5.0)


def test_on_job_finished_post_init() -> None:
    """on_job_finished() should register a callback after construction."""
    finished_jobs: list[ProductionJob] = []

    env = Environment()
    sf = ShopFloor(env=env)
    sf.on_job_finished(lambda job: finished_jobs.append(job))
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert finished_jobs == [job]


def test_post_init_hooks_combine_with_init_hooks() -> None:
    """Hooks registered post-init should execute after init hooks, in order."""
    from simulatte.typing import ProcessGenerator

    execution_order: list[str] = []

    def init_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("init")
        return
        yield

    def post_init_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> None:
        execution_order.append("post_init")

    env = Environment()
    sf = ShopFloor(env=env, on_after_operation=init_hook)
    sf.on_after_operation(post_init_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert execution_order == ["init", "post_init"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_shopfloor.py::test_on_before_operation_post_init tests/core/test_shopfloor.py::test_on_after_operation_post_init tests/core/test_shopfloor.py::test_on_job_finished_post_init tests/core/test_shopfloor.py::test_post_init_hooks_combine_with_init_hooks -v`
Expected: FAIL with `AttributeError: 'ShopFloor' object has no attribute 'on_before_operation'`

- [ ] **Step 3: Implement post-init registration methods**

In `src/simulatte/shopfloor.py`, add after the `_normalize_callbacks` static method (after line 737):

```python
    def on_before_operation(self, hook: OperationHook) -> None:
        """Register a hook to run before each operation.

        Hooks registered post-construction execute after any hooks
        passed via __init__, in registration order.
        """
        self._before_operation.append(hook)

    def on_after_operation(self, hook: OperationHook) -> None:
        """Register a hook to run after each operation.

        Hooks registered post-construction execute after any hooks
        passed via __init__, in registration order.
        """
        self._after_operation.append(hook)

    def on_job_finished(self, callback: Callable[[ProductionJob], None]) -> None:
        """Register a callback for when a job completes its entire routing.

        Callbacks registered post-construction execute after any callbacks
        passed via __init__, in registration order.
        """
        self._on_job_finished.append(callback)
```

- [ ] **Step 4: Run all shopfloor tests**

Run: `uv run pytest tests/core/test_shopfloor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/shopfloor.py tests/core/test_shopfloor.py
git commit -m "feat(shopfloor): add post-init hook registration methods"
```

---

### Task 6: PSP `release()` and `jobs_starting_at()`

**Files:**
- Modify: `src/simulatte/psp.py:10-14` (imports), `src/simulatte/psp.py` (add methods after `remove`)
- Test: `tests/core/test_psp.py`

- [ ] **Step 1: Write failing tests for `release()`**

Add to `tests/core/test_psp.py`:

```python
def test_psp_release_removes_and_adds_to_shopfloor() -> None:
    """release() should remove job from PSP, add to shopfloor, and start processing."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job)

    assert job in psp
    assert job not in sf.jobs

    psp.release(job)

    assert job not in psp
    assert job in sf.jobs
    assert sf.wip[server] == pytest.approx(5.0)

    env.run()
    assert job.done


def test_psp_release_sets_exit_timestamp() -> None:
    """release() should set psp_exit_at on the job."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job)
    psp.release(job)

    assert job.psp_exit_at == env.now


def test_psp_release_job_not_found() -> None:
    """release() should raise ValueError if job is not in the pool."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)

    with pytest.raises(ValueError, match="not found"):
        psp.release(job)
```

- [ ] **Step 2: Write failing tests for `jobs_starting_at()`**

Add to `tests/core/test_psp.py`:

```python
def test_psp_jobs_starting_at() -> None:
    """jobs_starting_at() should return only jobs whose routing starts at the given server."""
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    job_s1 = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[3, 4], due_date=20)
    job_s2 = ProductionJob(env=env, sku="B", servers=[server2, server1], processing_times=[3, 4], due_date=20)
    job_s1b = ProductionJob(env=env, sku="C", servers=[server1], processing_times=[5], due_date=20)

    psp.add(job_s1)
    psp.add(job_s2)
    psp.add(job_s1b)

    starting_at_s1 = psp.jobs_starting_at(server1)
    assert starting_at_s1 == [job_s1, job_s1b]

    starting_at_s2 = psp.jobs_starting_at(server2)
    assert starting_at_s2 == [job_s2]


def test_psp_jobs_starting_at_empty() -> None:
    """jobs_starting_at() should return empty list when no jobs match."""
    env = Environment()
    sf = ShopFloor(env=env)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server1], processing_times=[5], due_date=20)
    psp.add(job)

    assert psp.jobs_starting_at(server2) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_psp.py::test_psp_release_removes_and_adds_to_shopfloor tests/core/test_psp.py::test_psp_jobs_starting_at -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 4: Implement `release()` and `jobs_starting_at()`**

In `src/simulatte/psp.py`, first update the TYPE_CHECKING imports (after line 13):

```python
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from simulatte.job import ProductionJob
    from simulatte.server import Server
```

Then add after the `remove` method (after line 124):

```python
    def release(self, job: ProductionJob) -> None:
        """Remove a job from the pool and release it to the shopfloor.

        Combines remove() and shopfloor.add() in one atomic operation.
        Use remove() instead if you want to discard a job without releasing it.

        Args:
            job: The job to release from the pool to the shopfloor.

        Raises:
            ValueError: If the job is not found in the pool.
        """
        self.remove(job=job)
        self.shopfloor.add(job)

    def jobs_starting_at(self, server: Server) -> list[ProductionJob]:
        """Return jobs in the pool whose routing begins at the given server.

        Args:
            server: The server to filter by.

        Returns:
            List of jobs whose first routing server matches, in FIFO order.
        """
        return [job for job in self._psp if job.starts_at(server)]
```

- [ ] **Step 5: Run all PSP tests**

Run: `uv run pytest tests/core/test_psp.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/simulatte/psp.py tests/core/test_psp.py
git commit -m "feat(psp): add release() and jobs_starting_at() methods"
```

---

### Task 7: PSP `on_arrival()` event subscription

**Files:**
- Modify: `src/simulatte/psp.py` (add `_arrival_callbacks` list, modify `_signal_new_job`, add `on_arrival` method)
- Test: `tests/core/test_psp.py`

**Design note:** The Codex review identified that delegating to `on_arrival_trigger` (a SimPy process) requires "priming" — the process must reach its first `yield psp.new_job` before any arrivals are caught. This means `psp.on_arrival(cb); psp.add(job)` without an intermediate `env.run()` would silently miss the first job. Instead, we store callbacks directly on the PSP and invoke them synchronously from `_signal_new_job()`. This eliminates the priming requirement entirely and makes the API behave as users expect.

- [ ] **Step 1: Write failing tests for `on_arrival()`**

Add to `tests/core/test_psp.py`:

```python
def test_psp_on_arrival_callback() -> None:
    """on_arrival() should invoke callback each time a job is added."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    received: list[ProductionJob] = []

    def on_arrival(job: ProductionJob, pool: PreShopPool) -> None:
        received.append(job)

    psp.on_arrival(on_arrival)

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job1)

    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[5], due_date=20)
    psp.add(job2)

    assert received == [job1, job2]


def test_psp_on_arrival_no_priming_needed() -> None:
    """on_arrival() should work without env.run() between registration and add."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    received: list[ProductionJob] = []
    psp.on_arrival(lambda job, pool: received.append(job))

    # Add immediately without priming — callback should still fire
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job)

    assert received == [job]


def test_psp_on_arrival_multiple_callbacks() -> None:
    """Multiple on_arrival() callbacks should all be invoked in registration order."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    calls_a: list[ProductionJob] = []
    calls_b: list[ProductionJob] = []

    psp.on_arrival(lambda job, pool: calls_a.append(job))
    psp.on_arrival(lambda job, pool: calls_b.append(job))

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job)

    assert calls_a == [job]
    assert calls_b == [job]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_psp.py::test_psp_on_arrival_callback tests/core/test_psp.py::test_psp_on_arrival_no_priming_needed tests/core/test_psp.py::test_psp_on_arrival_multiple_callbacks -v`
Expected: FAIL with `AttributeError: 'PreShopPool' object has no attribute 'on_arrival'`

- [ ] **Step 3: Implement `on_arrival()`**

In `src/simulatte/psp.py`, update the TYPE_CHECKING imports to include `Callable`:

```python
if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterable

    from simulatte.job import ProductionJob
    from simulatte.server import Server
```

In `__init__`, add a callbacks list after `self.new_job = self.env.event()`:

```python
        self._arrival_callbacks: list[Callable[[ProductionJob, PreShopPool], None]] = []
```

Update `_signal_new_job` to invoke callbacks synchronously before triggering the SimPy event:

```python
    def _signal_new_job(self, job: ProductionJob) -> None:
        """Invoke arrival callbacks and trigger the new_job event.

        First invokes all registered on_arrival callbacks synchronously,
        then succeeds the SimPy new_job event (waking process-based listeners).

        Args:
            job: The job to pass to callbacks and as the event's value.
        """
        for callback in self._arrival_callbacks:
            callback(job, self)

        self.new_job.succeed(job)
        self.new_job = self.env.event()
```

Add the `on_arrival` method (at end of class, after `jobs_starting_at`):

```python
    def on_arrival(self, callback: Callable[[ProductionJob, PreShopPool], None]) -> None:
        """Subscribe a callback to be invoked each time a job arrives in the pool.

        Callbacks are invoked synchronously during add(), before the SimPy
        new_job event fires. No env.run() priming is needed.

        Args:
            callback: Function called with (job, psp) when a job arrives.
        """
        self._arrival_callbacks.append(callback)
```

- [ ] **Step 4: Run all PSP tests**

Run: `uv run pytest tests/core/test_psp.py -v`
Expected: All PASS (including existing `test_psp_signal_new_job_triggers_event` — SimPy event still fires after callbacks)

- [ ] **Step 5: Commit**

```bash
git add src/simulatte/psp.py tests/core/test_psp.py
git commit -m "feat(psp): add on_arrival() callback subscription (sync, no priming)"
```

---

### Task 8: Dispatcher protocol and `attach_dispatcher()`

**Files:**
- Modify: `src/simulatte/shopfloor.py` (add Protocol + method, update TYPE_CHECKING imports)
- Test: `tests/core/test_shopfloor.py`

- [ ] **Step 1: Write failing test for full dispatcher attachment**

Add to `tests/core/test_shopfloor.py`:

```python
def test_attach_dispatcher_full() -> None:
    """attach_dispatcher should wire all hooks when dispatcher has all methods."""
    from simulatte.psp import PreShopPool

    execution_log: list[str] = []

    class MyDispatcher:
        def on_before_operation(self, job, server, op_index, pt):
            execution_log.append("before_op")

        def on_after_operation(self, job, server, op_index, pt):
            execution_log.append("after_op")

        def on_job_finished(self, job):
            execution_log.append("job_finished")

        def on_psp_arrival(self, job, psp):
            execution_log.append("psp_arrival")

    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    psp = PreShopPool(env=env, shopfloor=sf)

    dispatcher = MyDispatcher()
    sf.attach_dispatcher(dispatcher, psp=psp)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    psp.add(job)

    # PSP arrival should have fired synchronously
    assert "psp_arrival" in execution_log

    # Release job to shopfloor
    psp.release(job)
    env.run()

    assert job.done
    assert "before_op" in execution_log
    assert "after_op" in execution_log
    assert "job_finished" in execution_log
```

- [ ] **Step 2: Write failing test for partial dispatcher**

```python
def test_attach_dispatcher_partial() -> None:
    """attach_dispatcher should wire only methods that exist on the dispatcher."""
    execution_log: list[str] = []

    class PartialDispatcher:
        def on_after_operation(self, job, server, op_index, pt):
            execution_log.append("after_op")

    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    dispatcher = PartialDispatcher()
    sf.attach_dispatcher(dispatcher)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert execution_log == ["after_op"]
```

- [ ] **Step 3: Write failing test for dispatcher without PSP**

```python
def test_attach_dispatcher_no_psp_skips_arrival() -> None:
    """attach_dispatcher without psp should skip on_psp_arrival wiring."""
    execution_log: list[str] = []

    class DispatcherWithArrival:
        def on_after_operation(self, job, server, op_index, pt):
            execution_log.append("after_op")

        def on_psp_arrival(self, job, psp):
            execution_log.append("psp_arrival")

    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    dispatcher = DispatcherWithArrival()
    sf.attach_dispatcher(dispatcher)  # no psp — on_psp_arrival should not be wired

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert execution_log == ["after_op"]
    assert "psp_arrival" not in execution_log
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_shopfloor.py::test_attach_dispatcher_full tests/core/test_shopfloor.py::test_attach_dispatcher_partial tests/core/test_shopfloor.py::test_attach_dispatcher_no_psp_skips_arrival -v`
Expected: FAIL with `AttributeError: 'ShopFloor' object has no attribute 'attach_dispatcher'`

- [ ] **Step 5: Implement Dispatcher protocol and `attach_dispatcher()`**

In `src/simulatte/shopfloor.py`, update the TYPE_CHECKING imports (after line 32) to include `PreShopPool`:

```python
if TYPE_CHECKING:  # pragma: no cover
    from simulatte.experimental.materials import MaterialCoordinator
    from simulatte.job import ProductionJob
    from simulatte.psp import PreShopPool
    from simulatte.server import Server
    from simulatte.typing import ProcessGenerator
```

Add the `Dispatcher` Protocol after `TimeSeriesCollector` (after line 222, before the built-in strategies section).

**Note:** This Protocol is deliberately NOT `@runtime_checkable`. All methods are optional at runtime — `attach_dispatcher` uses `getattr`/`callable` detection, not `isinstance`. The Protocol exists only for documentation and IDE discoverability. A partial dispatcher that implements just one method works fine with `attach_dispatcher`.

```python
class Dispatcher(Protocol):
    """Reference protocol showing the full dispatcher interface.

    All methods are optional at runtime — ``attach_dispatcher`` wires
    only those that are present and callable on the dispatcher object.

    This protocol is NOT runtime-checkable. It exists for documentation
    and IDE support. Partial implementations are explicitly supported.
    """

    def on_before_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator | None: ...

    def on_after_operation(
        self,
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator | None: ...

    def on_job_finished(self, job: ProductionJob) -> None: ...

    def on_psp_arrival(self, job: ProductionJob, psp: PreShopPool) -> None: ...
```

Add `attach_dispatcher` method on the `ShopFloor` class, after the `on_job_finished` registration method:

```python
    def attach_dispatcher(self, dispatcher: object, *, psp: PreShopPool | None = None) -> None:
        """Wire a dispatcher object's hook methods to this shopfloor.

        Detects which hook methods exist on the dispatcher and registers
        only those that are callable. This allows partial implementations
        where a dispatcher only handles a subset of events.

        Args:
            dispatcher: Object with any combination of on_before_operation,
                on_after_operation, on_job_finished, and on_psp_arrival methods.
            psp: If provided and dispatcher has on_psp_arrival, registers
                an arrival subscription on the PSP.
        """
        hook = getattr(dispatcher, "on_before_operation", None)
        if callable(hook):
            self.on_before_operation(hook)

        hook = getattr(dispatcher, "on_after_operation", None)
        if callable(hook):
            self.on_after_operation(hook)

        hook = getattr(dispatcher, "on_job_finished", None)
        if callable(hook):
            self.on_job_finished(hook)

        if psp is not None:
            hook = getattr(dispatcher, "on_psp_arrival", None)
            if callable(hook):
                psp.on_arrival(hook)
```

- [ ] **Step 6: Run all shopfloor tests**

Run: `uv run pytest tests/core/test_shopfloor.py -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/simulatte/shopfloor.py tests/core/test_shopfloor.py
git commit -m "feat(shopfloor): add Dispatcher protocol and attach_dispatcher()"
```

---

### Task 9: Final verification

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run type checking**

Run: `uv run ty check src/simulatte/`
Expected: No new errors introduced

- [ ] **Step 3: Run linter**

Run: `uv run pre-commit run --all-files`
Expected: All PASS
