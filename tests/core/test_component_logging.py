"""Component logging and the events that replace the built-in component log messages."""

from __future__ import annotations

from simulatte.entities import EntityCreated
from simulatte.environment import Environment
from simulatte.events import Event
from simulatte.job import ProductionJob
from simulatte.psp import PreShopPool, PspEntered, PspExited
from simulatte.router import Router
from simulatte.server import JobGranted, JobQueued, JobReleased, Server
from simulatte.shopfloor import JobFinished, OperationCompleted, OperationStarted, ShopFloor, ShopFloorEntered


class TestServerEvents:
    """Server resource events (they replace the Server debug log messages)."""

    @staticmethod
    def _run(until: float) -> tuple[Server, ProductionJob, list[Event]]:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, (JobQueued, JobGranted, JobReleased))
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[5.0],
            due_date=100.0,
        )

        sf.add(job)
        env.run(until=until)
        return server, job, seen

    def test_server_emits_queue_entry(self) -> None:
        server, job, seen = self._run(until=1)

        queued = [e for e in seen if isinstance(e, JobQueued)]
        assert len(queued) == 1
        event = queued[0]
        assert event.job == job.id
        assert event.server == server.id
        assert event.queue_length == 1
        assert event.priority == 0.0

    def test_server_grants_and_processes(self) -> None:
        server, job, seen = self._run(until=3)

        granted = [e for e in seen if isinstance(e, JobGranted)]
        assert [(e.job, e.server, e.t) for e in granted] == [(job.id, server.id, 0)]
        assert server.current_jobs == (job,)

        server.env.run(until=10)
        assert server.worked_time == 5.0

    def test_server_emits_job_released(self) -> None:
        server, job, seen = self._run(until=10)

        released = [e for e in seen if isinstance(e, JobReleased)]
        assert len(released) == 1
        event = released[0]
        assert event.job == job.id
        assert event.server == server.id
        granted = next(e for e in seen if isinstance(e, JobGranted))
        assert event.t - granted.t == 5.0  # time at server


class TestShopFloorEvents:
    """ShopFloor flow events (they replace the ShopFloor debug log messages)."""

    @staticmethod
    def _env() -> tuple[Environment, ShopFloor, list[Event]]:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, "*")
        return env, ShopFloor(env=env), seen

    def test_shopfloor_emits_job_entry(self) -> None:
        env, sf, seen = self._env()
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)

        sf.add(job)

        entries = [e for e in seen if isinstance(e, ShopFloorEntered)]
        assert len(entries) == 1
        event = entries[0]
        assert (event.job, event.shopfloor) == (job.id, sf.id)
        state = env.entities.snapshot()[sf.id]
        assert (state["wip"], state["jobs_in_system"]) == ({server.id: 5.0}, 1)  # the old wip_total, jobs_count

    def test_shopfloor_emits_job_finished(self) -> None:
        env, sf, seen = self._env()
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=100.0)

        sf.add(job)
        env.run(until=10)

        finished = [e for e in seen if isinstance(e, JobFinished)]
        assert len(finished) == 1
        event = finished[0]
        assert (event.job, event.shopfloor, event.t) == (job.id, sf.id, 5)
        assert (event.makespan, event.lateness, event.total_queue_time) == (5.0, -95.0, 0.0)

    def test_shopfloor_emits_operations(self) -> None:
        env, sf, seen = self._env()
        server1 = Server(env=env, capacity=1, shopfloor=sf)
        server2 = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(env=env, sku="A", servers=[server1, server2], processing_times=[3.0, 2.0], due_date=100.0)

        sf.add(job)
        env.run(until=10)

        queued = [e for e in seen if isinstance(e, JobQueued)]
        started = [e for e in seen if isinstance(e, OperationStarted)]
        completed = [e for e in seen if isinstance(e, OperationCompleted)]
        assert [(e.server, e.t) for e in queued] == [(server1.id, 0), (server2.id, 3)]
        assert [(e.server, e.op_index, e.t) for e in started] == [(server1.id, 0, 0), (server2.id, 1, 3)]
        assert [(e.server, e.op_index, e.processing_time, e.t) for e in completed] == [
            (server1.id, 0, 3.0, 3),
            (server2.id, 1, 2.0, 5),
        ]


class TestRouterAndPoolEvents:
    """Router and PreShopPool report through flow events (they replace their debug log messages)."""

    @staticmethod
    def _router(env: Environment, sf: ShopFloor, server: Server, psp: PreShopPool | None) -> Router:
        return Router(
            env=env,
            shopfloor=sf,
            servers=[server],
            psp=psp,
            inter_arrival_distribution=1.0,
            sku_distributions={"A": 1.0},
            sku_routings={"A": [server]},
            sku_service_times={"A": {server: 0.5}},
            due_date_offset_distribution={"A": 10.0},
        )

    def test_router_routes_to_shopfloor(self) -> None:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, (EntityCreated, PspEntered, ShopFloorEntered))
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        self._router(env, sf, server, psp=None)
        env.run(until=2.5)

        jobs = [e for e in seen if isinstance(e, EntityCreated) and e.kind == "job"]
        assert [e.t for e in jobs] == [1, 2]
        assert [(e.type_name, e.t) for e in seen if not isinstance(e, EntityCreated)] == [
            ("shopfloor.entered", 1),
            ("shopfloor.entered", 2),
        ]

    def test_router_routes_to_psp_and_pool_releases(self) -> None:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, (PspEntered, PspExited, ShopFloorEntered))
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        psp = PreShopPool(env=env, shopfloor=sf)
        self._router(env, sf, server, psp=psp)
        env.run(until=1.5)
        assert [(e.type_name, e.t) for e in seen] == [("psp.entered", 1)]

        job = psp[0]
        env.run(until=2.0)
        psp.release(job)
        assert [e.type_name for e in seen] == ["psp.entered", "psp.exited", "shopfloor.entered"]
        exited = seen[1]
        assert isinstance(exited, PspExited)
        assert (exited.job, exited.psp, exited.reason, exited.t) == (job.id, psp.id, "released", 2.0)
        assert job.time_in_psp == 1.0  # the old time_in_psp extra
        assert len(psp) == 0  # the old psp_size_after extra


class TestLoggingFiltering:
    """Tests for logging level filtering."""

    def test_debug_logs_filtered_at_info_level(self) -> None:
        env = Environment(log_level="INFO")
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        job = ProductionJob(
            env=env,
            sku="A",
            servers=[server],
            processing_times=[5.0],
            due_date=100.0,
        )

        sf.add(job)
        env.run(until=10)

        events = list(env.log_history)
        assert len(events) == 0
        env.close()


class TestIntegrationEvents:
    """The job lifecycle across components, through events; core components write no log messages."""

    def test_job_lifecycle_events(self) -> None:
        env = Environment(debug=True, log_level="DEBUG")
        try:
            seen: list[Event] = []
            env.bus.subscribe(seen.append, "*")
            sf = ShopFloor(env=env)
            server = Server(env=env, capacity=1, shopfloor=sf)
            job = ProductionJob(
                env=env,
                sku="A",
                servers=[server],
                processing_times=[5.0],
                due_date=100.0,
            )
            seen.clear()

            sf.add(job)
            env.run(until=10)

            assert [e.type_name for e in seen] == [
                "shopfloor.entered",
                "job.queued",
                "job.granted",
                "operation.started",
                "operation.completed",
                "shopfloor.wip_updated",
                "job.released",
                "job.finished",
                "entity.retired",
            ]
            timestamps = [e.t for e in seen]
            assert timestamps == sorted(timestamps)
            assert [e.seq for e in seen] == sorted(e.seq for e in seen)
            assert list(env.log_history) == []  # ShopFloor, Server, PreShopPool and Router no longer log
        finally:
            env.close()

    def test_multiple_jobs_events(self) -> None:
        env = Environment(debug=True)
        seen: list[Event] = []
        env.bus.subscribe(seen.append, "*")
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        jobs = []
        for i in range(3):
            job = ProductionJob(
                env=env,
                sku=f"SKU{i}",
                servers=[server],
                processing_times=[2.0],
                due_date=100.0,
            )
            jobs.append(job)
            sf.add(job)

        env.run(until=20)

        finished = [e for e in seen if isinstance(e, JobFinished)]
        assert [(e.job, e.t) for e in finished] == [(job.id, 2.0 * (i + 1)) for i, job in enumerate(jobs)]
        for job in jobs:
            job_events = [e.type_name for e in seen if getattr(e, "job", None) == job.id]
            assert job_events[0] == "shopfloor.entered"
            assert job_events[-1] == "job.finished"


def test_job_events_carry_full_job_ids() -> None:
    """Events carry the whole job id, so job-10000 never reads as job-1000."""
    env = Environment()
    sf = ShopFloor(env=env)
    server = Server(env=env, capacity=1, shopfloor=sf)
    for _ in range(10_000):
        ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[1.0], due_date=10.0)
    assert job.id == "job-10000"
    seen: list[Event] = []
    env.bus.subscribe(seen.append, "*")

    sf.add(job)
    env.run()

    job_ids = {getattr(e, "job", None) or getattr(e, "entity", None) for e in seen}
    assert job_ids == {"job-10000", None}  # None: shopfloor.wip_updated has no job
    assert len(seen) == 9
