from __future__ import annotations

from simpy.resources.resource import Request

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server, ServerPriorityRequest
from simulatte.shopfloor import ShopFloor


def _as_priority_request(r: Request) -> ServerPriorityRequest:
    assert isinstance(r, ServerPriorityRequest)
    return r


class TestServerPriorityRequest:
    """Tests for ServerPriorityRequest."""

    def test_repr(self) -> None:
        """ServerPriorityRequest should have a useful repr."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        request = ServerPriorityRequest(server, job)
        repr_str = repr(request)

        assert "ServerPriorityRequest" in repr_str
        assert "job=" in repr_str
        assert "server=" in repr_str


class TestServer:
    """Tests for Server class."""

    def test_repr(self) -> None:
        """Server should have a useful repr."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        repr_str = repr(server)
        assert "Server" in repr_str
        assert "id=" in repr_str

    def test_repr_without_shopfloor(self) -> None:
        """Server created without shopfloor should have id=-1."""
        env = Environment()
        server = Server(env=env, capacity=1, shopfloor=None)

        assert "id=-1" in repr(server)

    def test_average_queue_length_at_t0(self) -> None:
        """average_queue_length should return 0.0 at t=0."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        assert server.average_queue_length == 0.0

    def test_utilization_rate_at_t0(self) -> None:
        """utilization_rate should return 0 at t=0."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        assert server.utilization_rate == 0

    def test_queueing_jobs(self) -> None:
        """queueing_jobs should yield jobs waiting in queue."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
        job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100], due_date=200)

        sf.add(job1)
        sf.add(job2)
        env.run(until=1)  # job1 processing, job2 in queue

        queueing = list(server.queueing_jobs)
        assert job2 in queueing
        assert job1 not in queueing  # job1 is processing, not queuing

    def test_time_series_collection(self) -> None:
        """Server with collect_time_series=True should track queue and utilization."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf, collect_time_series=True)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        sf.add(job)
        env.run()

        # Qt and Ut should have data
        assert server._qt is not None and len(server._qt) > 0
        assert server._ut is not None and len(server._ut) > 0

    def test_time_series_not_collected_by_default(self) -> None:
        """Server without collect_time_series should not track time series."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf, collect_time_series=False)

        assert server._qt is None
        assert server._ut is None

    def test_update_ut_no_change(self) -> None:
        """_update_ut should not add duplicate entries for same status."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf, collect_time_series=True)

        initial_len = len(server._ut) if server._ut else 0

        # Call _update_ut twice with same status - should not add duplicate
        server._update_ut()
        server._update_ut()

        # Should still have same length (no duplicate 0.0 entries)
        assert len(server._ut) if server._ut else 0 == initial_len

    def test_process_job_with_history(self) -> None:
        """Server with retain_job_history=True should track processed jobs."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf, retain_job_history=True)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5], due_date=100)

        sf.add(job)
        env.run()

        assert server._jobs is not None
        assert job in server._jobs

    def test_sort_queue(self) -> None:
        """sort_queue should sort queue by priority key."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        class DummyRequest:
            def __init__(self, *, key: float, job: ProductionJob) -> None:
                self.key = key
                self.job = job

        # Create jobs with different priorities (floats to verify no int truncation)
        job_med = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 10.3,
        )
        job_low = ProductionJob(
            env=env,
            sku="B",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 5.7,
        )
        job_high = ProductionJob(
            env=env,
            sku="C",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 15.1,
        )

        # Manually build an intentionally unsorted queue
        req_high = DummyRequest(key=job_high.priority(server), job=job_high)
        req_low = DummyRequest(key=job_low.priority(server), job=job_low)
        req_med = DummyRequest(key=job_med.priority(server), job=job_med)
        server.queue[:] = [req_high, req_low, req_med]

        assert [req.job for req in server.queue] == [job_high, job_low, job_med]

        # Sort the queue
        server.sort_queue()

        assert [req.job for req in server.queue] == [job_low, job_med, job_high]

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

    def test_empty_property(self) -> None:
        """empty should be True when queue is empty."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        assert server.empty

        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
        sf.add(job)
        env.run(until=0.1)

        # Job is processing but queue might be empty
        # Server.empty checks queue length, not processing count
        assert server.empty  # Queue is empty, job is processing

    def test_empty_with_queue(self) -> None:
        """empty should be False when queue has waiting jobs."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job1 = ProductionJob(env=env, sku="A", servers=[server], processing_times=[100], due_date=200)
        job2 = ProductionJob(env=env, sku="B", servers=[server], processing_times=[100], due_date=200)

        sf.add(job1)
        sf.add(job2)
        env.run(until=0.1)

        # job1 processing, job2 in queue
        assert not server.empty


class TestPriorityQueueOrdering:
    """Tests for dynamic priority-based queue ordering in Server.

    SimPy's PriorityResource uses a SortedQueue that re-sorts on every
    insertion. These tests verify that ServerPriorityRequest integrates
    correctly with this mechanism: jobs enter the queue sorted by their
    priority value (lower = more urgent), and late-arriving jobs are
    inserted in the correct position among already-queued jobs.
    """

    @staticmethod
    def _make_job(
        env: Environment,
        server: Server,
        sku: str,
        priority: float,
        processing_time: float = 3.0,
    ) -> ProductionJob:
        def policy(job: ProductionJob, srv: Server) -> float:
            return priority

        return ProductionJob(
            env=env,
            sku=sku,
            servers=[server],
            processing_times=[processing_time],
            due_date=1000.0,
            priority_policy=policy,
        )

    def test_initial_insertion_sorts_by_priority(self) -> None:
        """Jobs entering the queue are sorted by priority, not insertion order."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=100.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_b = self._make_job(env, server, "B", 3.14)
        job_a = self._make_job(env, server, "A", 2.11)
        sf.add(job_b)
        sf.add(job_a)
        env.run(until=0.02)

        queued_skus = [_as_priority_request(r).job.sku for r in server.queue]
        assert queued_skus == ["A", "B"]

    def test_late_arrival_inserted_in_priority_order(self) -> None:
        """A job arriving after others are queued is inserted at the correct position."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=100.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_low = self._make_job(env, server, "LOW", 10.0)
        job_high = self._make_job(env, server, "HIGH", 1.0)
        sf.add(job_low)
        sf.add(job_high)
        env.run(until=0.02)

        # Now add MED between HIGH and LOW
        job_med = self._make_job(env, server, "MED", 5.0)
        sf.add(job_med)
        env.run(until=0.03)

        queued_skus = [_as_priority_request(r).job.sku for r in server.queue]
        assert queued_skus == ["HIGH", "MED", "LOW"]

    def test_dynamic_reordering_during_processing(self) -> None:
        """A job added while another is processing is correctly sorted among waiters.

        Scenario:
        1. Blocker occupies the server
        2. B (priority 3.14) and A (priority 2.11) enter queue → sorted [A, B]
        3. Blocker finishes → A starts processing, B remains in queue
        4. C (priority 1.5) arrives → sorted before B in queue → [C, B]
        5. Processing order: Blocker → A → C → B
        """
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_b = self._make_job(env, server, "B", 3.14)
        job_a = self._make_job(env, server, "A", 2.11)
        sf.add(job_b)
        sf.add(job_a)
        env.run(until=0.02)

        # Queue should be [A, B]
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["A", "B"]

        # Blocker finishes at t=10, A gets the server
        env.run(until=10.01)
        assert [_as_priority_request(r).job.sku for r in server.users] == ["A"]
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["B"]

        # Add C (more urgent than B) while A is processing
        job_c = self._make_job(env, server, "C", 1.5)
        sf.add(job_c)
        env.run(until=10.02)

        assert [_as_priority_request(r).job.sku for r in server.queue] == ["C", "B"]

        # Run to completion and verify processing order
        env.run()
        blocker_exit = blocker.servers_exit_at[server]
        a_exit = job_a.servers_exit_at[server]
        c_exit = job_c.servers_exit_at[server]
        b_exit = job_b.servers_exit_at[server]
        assert blocker_exit is not None and a_exit is not None
        assert c_exit is not None and b_exit is not None
        assert blocker_exit < a_exit
        assert a_exit < c_exit
        assert c_exit < b_exit

    def test_equal_priority_preserves_arrival_order(self) -> None:
        """Jobs with the same priority are processed in FIFO (arrival) order."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_first = self._make_job(env, server, "FIRST", 5.0)
        sf.add(job_first)
        env.run(until=0.02)

        job_second = self._make_job(env, server, "SECOND", 5.0)
        sf.add(job_second)
        env.run(until=0.03)

        # Same priority → FIFO: FIRST entered at t=0.01, SECOND at t=0.02
        env.run()
        first_exit = job_first.servers_exit_at[server]
        second_exit = job_second.servers_exit_at[server]
        assert first_exit is not None and second_exit is not None
        assert first_exit < second_exit

    def test_fractional_priority_discrimination(self) -> None:
        """Priorities differing by less than 1.0 must still be distinguished.

        Regression test: under int() truncation, 5.2 and 5.8 would both
        become 5, losing the ordering guarantee.
        """
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        # Add in REVERSE priority order to ensure sorting, not FIFO
        job_less_urgent = self._make_job(env, server, "LESS", 5.8)
        job_more_urgent = self._make_job(env, server, "MORE", 5.2)
        sf.add(job_less_urgent)
        sf.add(job_more_urgent)

        env.run()

        more_exit = job_more_urgent.servers_exit_at[server]
        less_exit = job_less_urgent.servers_exit_at[server]
        assert more_exit is not None and less_exit is not None
        assert more_exit < less_exit

    def test_negative_priorities_sort_correctly(self) -> None:
        """Negative priority values (urgent jobs) sort before positive ones."""
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=10.0)
        sf.add(blocker)
        env.run(until=0.01)

        job_positive = self._make_job(env, server, "POS", 3.0)
        job_negative = self._make_job(env, server, "NEG", -2.0)
        sf.add(job_positive)
        sf.add(job_negative)

        env.run(until=0.02)
        assert [_as_priority_request(r).job.sku for r in server.queue] == ["NEG", "POS"]

        env.run()
        neg_exit = job_negative.servers_exit_at[server]
        pos_exit = job_positive.servers_exit_at[server]
        assert neg_exit is not None and pos_exit is not None
        assert neg_exit < pos_exit

    def test_multiple_late_arrivals_maintain_order(self) -> None:
        """Multiple jobs arriving at different times are all correctly sorted.

        Jobs arrive one at a time while a blocker is processing:
        D(8.0), B(3.0), E(9.0), A(1.0), C(5.0)
        Queue at each step should be sorted, and processing order should be
        A → B → C → D → E.
        """
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = self._make_job(env, server, "BLOCK", 0, processing_time=100.0)
        sf.add(blocker)
        env.run(until=0.01)

        arrivals = [("D", 8.0), ("B", 3.0), ("E", 9.0), ("A", 1.0), ("C", 5.0)]
        jobs = {}
        for i, (sku, prio) in enumerate(arrivals):
            j = self._make_job(env, server, sku, prio)
            sf.add(j)
            jobs[sku] = j
            env.run(until=0.02 + i * 0.01)

        queued_skus = [_as_priority_request(r).job.sku for r in server.queue]
        assert queued_skus == ["A", "B", "C", "D", "E"]

        env.run()
        exit_times = {sku: j.servers_exit_at[server] for sku, j in jobs.items()}
        assert all(v is not None for v in exit_times.values())
        assert exit_times["A"] < exit_times["B"] < exit_times["C"] < exit_times["D"] < exit_times["E"]


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
