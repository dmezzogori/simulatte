from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def test_single_job_processing() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)

    assert job in sf.jobs
    assert not sf.jobs_done
    assert sf.wip[server] == pytest.approx(5)

    env.run()

    assert job.done
    assert job.psp_exit_at == pytest.approx(0)
    assert job.finished_at == pytest.approx(5)
    assert job in sf.jobs_done
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(5)
    assert server.utilization_rate == pytest.approx(1.0)
    assert server.idle_time == pytest.approx(0.0)


def test_multiple_jobs_sequential_processing_and_queue() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[3], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[4], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(7)
    assert server.count == 0

    env.run()

    assert job1.done
    assert job2.done
    assert job1.finished_at == pytest.approx(3)
    assert job2.finished_at == pytest.approx(7)
    assert sf.jobs_done == [job1, job2]
    assert sf.wip[server] == pytest.approx(0)

    assert server.worked_time == pytest.approx(7)
    assert server.average_queue_length == (1 * 3 + 0 * 4) / 7
    assert server.utilization_rate == 1


def test_parallel_processing_with_capacity() -> None:
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=2, shopfloor=sf)
    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[5], due_date=10)
    sf.add(job1)
    sf.add(job2)

    assert sf.wip[server] == pytest.approx(10)

    env.run()

    assert job1.finished_at == pytest.approx(5)
    assert job2.finished_at == pytest.approx(5)
    assert env.now == pytest.approx(5)

    assert server.worked_time == pytest.approx(10)
    assert server.utilization_rate == pytest.approx(2.0)


def test_corrected_wip_strategy() -> None:
    from simulatte.shopfloor import CorrectedWIPStrategy

    env = Environment()
    shopfloor = ShopFloor(env=env, wip_strategy=CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server2 = Server(env=env, capacity=1, shopfloor=shopfloor)
    server3 = Server(env=env, capacity=1, shopfloor=shopfloor)
    job1 = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[2, 3], due_date=10)
    job2 = ProductionJob(env=env, sku="B", servers=[server2, server3], processing_times=[4, 5], due_date=10)
    shopfloor.add(job1)
    shopfloor.add(job2)

    assert shopfloor.wip[server1] == 2
    assert shopfloor.wip[server2] == 5.5
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job1.current_server == server2
    assert job1.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 7
    assert shopfloor.wip[server3] == 2.5

    env.run(until=shopfloor.job_processing_end)
    assert job2.current_server == server3
    assert job2.remaining_routing == ()

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 3
    assert shopfloor.wip[server3] == 5

    env.run(until=shopfloor.job_processing_end)
    assert job1.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 5

    env.run()
    assert job2.done

    assert shopfloor.wip[server1] == 0
    assert shopfloor.wip[server2] == 0
    assert shopfloor.wip[server3] == 0


def test_average_time_in_system_no_jobs_done() -> None:
    """average_time_in_system should return 0.0 when no jobs are done."""
    env = Environment()
    sf = ShopFloor(env=env)
    Server(env=env, capacity=1, shopfloor=sf)

    assert sf.average_time_in_system == 0.0


def test_average_time_in_system_with_jobs() -> None:
    """average_time_in_system should calculate correctly when jobs are done."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[2.0], due_date=100)
    job2 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[4.0], due_date=100)
    sf.add(job1)
    sf.add(job2)
    env.run()

    # job1 time_in_system = 2.0 (exit at t=2, enter at t=0)
    # job2 time_in_system = 6.0 (exit at t=6, enter at t=0)
    # average = (2 + 6) / 2 = 4.0
    assert sf.average_time_in_system == pytest.approx(4.0)


# =============================================================================
# Tests for new extensibility features
# =============================================================================


def test_before_operation_hook_adds_setup_time() -> None:
    """before_operation hook should inject time before processing."""
    from simulatte.typing import ProcessGenerator

    setup_times: list[float] = []

    def setup_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        setup_times.append(server.env.now)
        yield server.env.timeout(2.0)  # 2s setup time

    env = Environment()
    sf = ShopFloor(env=env, on_before_operation=setup_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    # Total time = setup (2) + processing (5) = 7
    assert job.finished_at == pytest.approx(7.0)
    assert len(setup_times) == 1
    assert setup_times[0] == pytest.approx(0.0)  # Hook ran at t=0


def test_after_operation_hook_executes_after_processing() -> None:
    """after_operation hook should run after processing completes."""
    from simulatte.typing import ProcessGenerator

    hook_times: list[float] = []

    def after_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        hook_times.append(server.env.now)
        yield server.env.timeout(1.0)  # 1s cleanup

    env = Environment()
    sf = ShopFloor(env=env, on_after_operation=after_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    # Hook runs after processing (at t=5), then cleanup takes 1s
    # But job.finished_at is set before the after hooks complete
    assert len(hook_times) == 1
    assert hook_times[0] == pytest.approx(5.0)  # Hook ran after processing


def test_multiple_hooks_execute_in_order() -> None:
    """Multiple hooks should execute in order."""
    from simulatte.shopfloor import OperationHook
    from simulatte.typing import ProcessGenerator

    execution_order: list[str] = []

    def hook1(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("hook1")
        return
        yield  # Make it a generator

    def hook2(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("hook2")
        return
        yield

    hooks: list[OperationHook] = [hook1, hook2]  # ty: ignore[invalid-assignment]
    env = Environment()
    sf = ShopFloor(env=env, on_before_operation=hooks)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert execution_order == ["hook1", "hook2"]


def test_wip_strategy_corrected_via_constructor() -> None:
    """CorrectedWIPStrategy via constructor should work like deprecated flag."""
    from simulatte.shopfloor import CorrectedWIPStrategy

    env = Environment()
    sf = ShopFloor(env=env, wip_strategy=CorrectedWIPStrategy())
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[2, 4], due_date=10)
    sf.add(job)

    # With corrected WIP: server1 gets 2/1=2, server2 gets 4/2=2
    assert sf.wip[server1] == pytest.approx(2.0)
    assert sf.wip[server2] == pytest.approx(2.0)

    env.run()
    assert job.done
    assert sf.wip[server1] == pytest.approx(0.0)
    assert sf.wip[server2] == pytest.approx(0.0)


def test_on_job_finished_callback() -> None:
    """on_job_finished callback should be called when job completes."""
    finished_jobs: list[ProductionJob] = []

    def on_finished(job: ProductionJob) -> None:
        finished_jobs.append(job)

    env = Environment()
    sf = ShopFloor(env=env, on_job_finished=on_finished)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert len(finished_jobs) == 1
    assert finished_jobs[0] is job


def test_multiple_on_job_finished_callbacks() -> None:
    """Multiple on_job_finished callbacks should all be called."""
    callback1_count = [0]
    callback2_count = [0]

    def cb1(job: ProductionJob) -> None:
        callback1_count[0] += 1

    def cb2(job: ProductionJob) -> None:
        callback2_count[0] += 1

    env = Environment()
    sf = ShopFloor(env=env, on_job_finished=[cb1, cb2])
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=10)
    sf.add(job)
    env.run()

    assert callback1_count[0] == 1
    assert callback2_count[0] == 1


def test_hooks_with_multi_server_routing() -> None:
    """Hooks should be called for each operation in multi-server routing."""
    from simulatte.typing import ProcessGenerator

    hook_calls: list[tuple[str, int]] = []  # (server id, op_index)

    def track_hook(
        job: ProductionJob,
        server: Server,
        op_index: int,
        processing_time: float,
    ) -> ProcessGenerator:
        hook_calls.append((server.id, op_index))
        return
        yield

    env = Environment()
    sf = ShopFloor(env=env, on_before_operation=track_hook)
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)
    server3 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(
        env=env,
        sku="A",
        servers=[server1, server2, server3],
        processing_times=[2, 3, 4],
        due_date=20,
    )
    sf.add(job)
    env.run()

    assert hook_calls == [(server1.id, 0), (server2.id, 1), (server3.id, 2)]


# =============================================================================
# Sync OperationHook Tests
# =============================================================================


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
    sf = ShopFloor(env=env, on_before_operation=sync_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1
    assert hook_calls[0] == 0.0
    assert job.finished_at == pytest.approx(5.0)


def test_mixed_sync_and_generator_hooks_execute_in_order() -> None:
    """Sync and generator hooks in the same list execute in registration order."""
    from simulatte.shopfloor import OperationHook
    from simulatte.typing import ProcessGenerator

    execution_order: list[str] = []

    def sync_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> None:
        execution_order.append("sync")

    def gen_hook(job: ProductionJob, server: Server, op_index: int, pt: float) -> ProcessGenerator:
        execution_order.append("gen")
        yield server.env.timeout(0.1)

    env = Environment()
    hooks: list[OperationHook] = [sync_hook, gen_hook]  # ty: ignore[invalid-assignment]
    sf = ShopFloor(env=env, on_before_operation=hooks)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert execution_order == ["sync", "gen"]
    assert job.finished_at == pytest.approx(5.1)


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
    sf = ShopFloor(env=env, on_after_operation=sync_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert job.done
    assert len(hook_calls) == 1
    assert hook_calls[0] == pytest.approx(5.0)


# =============================================================================
# Post-init hook registration Tests
# =============================================================================


def test_on_before_operation_post_init() -> None:
    """on_before_operation() should register a hook after construction."""
    hook_calls: list[float] = []

    def hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> None:
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

    def hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> None:
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

    def init_hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> ProcessGenerator:
        execution_order.append("init")
        return
        yield

    def post_init_hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> None:
        execution_order.append("post_init")

    env = Environment()
    sf = ShopFloor(env=env, on_after_operation=init_hook)
    sf.on_after_operation(post_init_hook)
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert execution_order == ["init", "post_init"]


# =============================================================================
# on_processing_end Tests
# =============================================================================


def test_on_processing_end_fires_after_each_operation() -> None:
    """on_processing_end callback should fire after each operation with (job, server)."""
    completions: list[tuple[ProductionJob, Server]] = []

    env = Environment()
    sf = ShopFloor(env=env)
    sf.on_processing_end(lambda job, server: completions.append((job, server)))
    server1 = Server(env=env, capacity=1, shopfloor=sf)
    server2 = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[3, 4], due_date=20)
    sf.add(job)
    env.run()

    assert len(completions) == 2
    assert completions[0] == (job, server1)
    assert completions[1] == (job, server2)


def test_on_processing_end_multiple_callbacks_in_order() -> None:
    """Multiple on_processing_end callbacks should fire in registration order."""
    order: list[str] = []

    env = Environment()
    sf = ShopFloor(env=env)
    sf.on_processing_end(lambda job, server: order.append("first"))
    sf.on_processing_end(lambda job, server: order.append("second"))
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert order == ["first", "second"]


def test_on_processing_end_fires_after_server_release() -> None:
    """on_processing_end should fire after server is released (exit_at stamped, previous_server available)."""
    callback_time: list[float] = []
    exit_stamped: list[bool] = []
    prev_server: list[Server | None] = []
    server_idle: list[bool] = []

    env = Environment()
    sf = ShopFloor(env=env)

    def check_release(job: ProductionJob, server: Server) -> None:
        callback_time.append(env.now)
        exit_stamped.append(job.servers_exit_at[server] is not None)
        prev_server.append(job.previous_server)
        server_idle.append(server.is_idle)

    sf.on_processing_end(check_release)
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert callback_time == [5.0]
    assert exit_stamped == [True]
    assert prev_server == [server]
    assert server_idle == [True]


def test_on_processing_end_via_attach_dispatcher() -> None:
    """attach_dispatcher should wire on_processing_end if present."""
    completions: list[tuple[ProductionJob, Server]] = []

    class MyDispatcher:
        def on_processing_end(self, job, server):
            completions.append((job, server))

    env = Environment()
    sf = ShopFloor(env=env)
    sf.attach_dispatcher(MyDispatcher())
    server = Server(env=env, capacity=1, shopfloor=sf)

    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)
    env.run()

    assert completions == [(job, server)]


# =============================================================================
# Dispatcher Protocol and attach_dispatcher Tests
# =============================================================================


def test_attach_dispatcher_full() -> None:
    """attach_dispatcher should wire all hooks when dispatcher has all methods."""
    from simulatte.psp import PreShopPool

    execution_log: list[str] = []

    class MyDispatcher:
        def on_before_operation(self, job, server, op_index, processing_time):
            execution_log.append("before_op")

        def on_after_operation(self, job, server, op_index, processing_time):
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


def test_attach_dispatcher_partial() -> None:
    """attach_dispatcher should wire only methods that exist on the dispatcher."""
    execution_log: list[str] = []

    class PartialDispatcher:
        def on_after_operation(self, job, server, op_index, processing_time):
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


def test_attach_dispatcher_no_psp_skips_arrival() -> None:
    """attach_dispatcher without psp should skip on_psp_arrival wiring."""
    execution_log: list[str] = []

    class DispatcherWithArrival:
        def on_after_operation(self, job, server, op_index, processing_time):
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


def test_hook_returning_non_generator_raises_type_error() -> None:
    """A hook that returns a non-None, non-generator value should raise TypeError."""

    def bad_hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> int:
        return 42

    env = Environment()
    sf = ShopFloor(env=env, on_before_operation=bad_hook)  # ty: ignore[invalid-argument-type]
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)

    with pytest.raises(TypeError, match="OperationHook must return None or a generator"):
        env.run()


def test_after_hook_returning_non_generator_raises_type_error() -> None:
    """An after-operation hook that returns non-None/non-generator should raise TypeError."""

    def bad_hook(job: ProductionJob, server: Server, op_index: int, processing_time: float) -> str:
        return "oops"

    env = Environment()
    sf = ShopFloor(env=env, on_after_operation=bad_hook)  # ty: ignore[invalid-argument-type]
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=20)
    sf.add(job)

    with pytest.raises(TypeError, match="OperationHook must return None or a generator"):
        env.run()


def test_attach_dispatcher_without_on_psp_arrival() -> None:
    """Dispatcher without on_psp_arrival attribute skips PSP wiring."""
    from simulatte.psp import PreShopPool

    env = Environment()
    sf = ShopFloor(env=env)
    psp = PreShopPool(env=env, shopfloor=sf)

    class MinimalDispatcher:
        pass

    sf.attach_dispatcher(MinimalDispatcher(), psp=psp)
    # No error — the missing on_psp_arrival is simply skipped
