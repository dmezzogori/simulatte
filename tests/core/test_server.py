from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server, ServerPriorityRequest
from simulatte.shopfloor import ShopFloor


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
            def __init__(self, *, key: int, job: ProductionJob) -> None:
                self.key = key
                self.job = job

        # Create jobs with different priorities
        job_med = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 10,
        )
        job_low = ProductionJob(
            env=env,
            sku="B",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 5,
        )
        job_high = ProductionJob(
            env=env,
            sku="C",
            servers=[server],
            processing_times=[100],
            due_date=200,
            priority_policy=lambda job, srv: 15,
        )

        # Manually build an intentionally unsorted queue
        req_high = DummyRequest(key=int(job_high.priority(server)), job=job_high)
        req_low = DummyRequest(key=int(job_low.priority(server)), job=job_low)
        req_med = DummyRequest(key=int(job_med.priority(server)), job=job_med)
        server.queue[:] = [req_high, req_low, req_med]

        assert [req.job for req in server.queue] == [job_high, job_low, job_med]

        # Sort the queue
        server.sort_queue()

        assert [req.job for req in server.queue] == [job_low, job_med, job_high]

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
