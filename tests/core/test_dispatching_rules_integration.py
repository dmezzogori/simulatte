"""Integration tests driving dispatch order through the real Server queue.

The per-family unit tests (``test_processing_rules.py``, ``test_due_date_rules.py``,
``test_slack_rules.py``) check each rule's returned float in isolation.
These tests instead wire a rule in as a job's ``priority_policy`` and run an
actual simulation, asserting that jobs come out of a single contended queue in
the order the rule prescribes. The mechanism under test is
``Server.sort_queue``, which re-evaluates ``job.priority(server)`` for every
queued request at each dispatch decision and serves the lowest value first.

Pattern (mirrors the FCFS arrival-order test in ``test_processing_rules.py``): seize
the server with a long blocker, pile up jobs behind it, run to completion, then
assert the processing order via ``job.servers_exit_at[server]``. Each scenario
is built so the expected order differs from the jobs' arrival order — otherwise
a degenerate rule returning a constant would pass via the entry-time tiebreak.
"""

from __future__ import annotations

from simulatte.dispatching_rules import (
    apparent_tardiness_cost,
    cost_over_time,
    critical_ratio,
    earliest_due_date,
    modified_operational_due_date,
    operational_due_date,
    planned_slack_time,
    raghu_rajendran,
    shortest_processing_time,
    slack_per_remaining_operation,
    work_in_next_queue,
)
from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

BLOCKER_PROCESSING_TIME = 100.0
ARRIVAL_STEP = 0.01


def _processed_order(jobs: list[ProductionJob], server: Server) -> list[str]:
    """Return the SKUs of *jobs* sorted by their exit time at *server*."""
    keyed = sorted(jobs, key=lambda job: _exit_at(job, server))
    return [job.sku for job in keyed]


def _exit_at(job: ProductionJob, server: Server) -> float:
    """Return the recorded exit timestamp for *job* at *server* (must be set)."""
    exit_time = job.servers_exit_at[server]
    assert exit_time is not None, f"job {job.sku} never exited {server}"
    return float(exit_time)


class TestShortestProcessingTimeIntegration:
    """SPT: jobs leave the contended queue shortest-processing-time first."""

    def test_dispatches_shortest_processing_time_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=shortest_processing_time,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # SPT key = job.routing[server]. Ascending: B(2) < A(5) < C(8).
        for sku, pt in [("A", 5.0), ("B", 2.0), ("C", 8.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[pt],
                due_date=100.0,
                priority_policy=shortest_processing_time,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["B", "A", "C"]


class TestEarliestDueDateIntegration:
    """EDD: jobs leave the contended queue earliest-due-date first."""

    def test_dispatches_earliest_due_date_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=earliest_due_date,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # EDD key = job.due_date (server-agnostic). Ascending: B(30) < C(40) < A(50).
        for sku, due_date in [("A", 50.0), ("B", 30.0), ("C", 40.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[1.0],
                due_date=due_date,
                priority_policy=earliest_due_date,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["B", "C", "A"]


class TestOperationalDueDateIntegration:
    """ODD: routing length reorders jobs relative to their raw due dates.

    Distinguishes ODD from EDD: the jobs are built so the ``d / |R_i|`` term
    produces an order different from sorting by due date alone.
    """

    def test_dispatches_lowest_operational_due_date_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=operational_due_date,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # ODD at s1 = t_r + 1 * max(0, (d - t_r) / |R_i|), t_r ~= 0, so ODD ~= d / |R_i|.
        #   A: |R|=1, d=60  -> 60      C: |R|=2, d=80  -> 40      B: |R|=3, d=90  -> 30
        # ODD ascending: B(30) < C(40) < A(60)  ->  ["B", "C", "A"].
        # EDD (by due date) would be A(60) < C(80) < B(90) -> ["A", "C", "B"]: different,
        # so this exercises ODD's routing-length normalization, not just due dates.
        for sku, servers, pts, due_date in [
            ("A", [s1], [1.0], 60.0),
            ("B", [s1, s2, s3], [1.0, 1.0, 1.0], 90.0),
            ("C", [s1, s2], [1.0, 1.0], 80.0),
        ]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=servers,
                processing_times=pts,
                due_date=due_date,
                priority_policy=operational_due_date,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["B", "C", "A"]


class TestModifiedOperationalDueDateIntegration:
    """MODD = max(ODD, now + p_ij): exercise both branches of the max()."""

    def test_late_jobs_dispatch_by_processing_time(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=modified_operational_due_date,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Jobs are created near t=0 with due_date=5: positive creation-time slack, so
        # ODD = t_r + (d - t_r) = d = 5 (single op, no clamp). They dispatch near t=100,
        # where now+p (~100+p) >> ODD, so MODD = max(ODD, now+p) = now+p. Same now per
        # dispatch -> order by p ascending: B(1) < C(2) < A(3). (now+p branch.)
        for sku, pt in [("A", 3.0), ("B", 1.0), ("C", 2.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[pt],
                due_date=5.0,
                priority_policy=modified_operational_due_date,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["B", "C", "A"]

    def test_slack_dominated_jobs_dispatch_by_operational_due_date(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=modified_operational_due_date,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Every ODD exceeds now+p (~101) at dispatch, so MODD = ODD (the slack branch).
        # ODD at s1 ~= d / |R_i|:
        #   A: |R|=1, d=130 -> 130     B: |R|=2, d=300 -> 150     C: |R|=3, d=360 -> 120
        # MODD ascending: C(120) < A(130) < B(150) -> ["C", "A", "B"].
        # EDD would be A(130) < B(300) < C(360) -> ["A", "B", "C"]: different, so this
        # confirms MODD is dispatching on ODD here, not on raw due date or SPT.
        for sku, servers, pts, due_date in [
            ("A", [s1], [1.0], 130.0),
            ("B", [s1, s2], [1.0, 1.0], 300.0),
            ("C", [s1, s2, s3], [1.0, 1.0, 1.0], 360.0),
        ]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=servers,
                processing_times=pts,
                due_date=due_date,
                priority_policy=modified_operational_due_date,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["C", "A", "B"]


class TestCriticalRatioIntegration:
    """CR: jobs leave the queue ordered by slack / remaining processing time."""

    def test_dispatches_lowest_critical_ratio_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=critical_ratio,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # CR = (d - now) / remaining_pt, now ~= 100 at the first dispatch and the
        # gaps below dwarf the ~1-unit drift as later jobs are served.
        # A: (300 - 100) / 4 = 50, B: (160 - 100) / 2 = 30, C: (1200 - 100) / 10 = 110.
        # Ascending: B < A < C.
        for sku, due_date, pts in [
            ("A", 300.0, [2.0, 2.0]),
            ("B", 160.0, [1.0, 1.0]),
            ("C", 1200.0, [5.0, 5.0]),
        ]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, s2],
                processing_times=pts,
                due_date=due_date,
                priority_policy=critical_ratio,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["B", "A", "C"]


class TestPlannedSlackTimeIntegration:
    """PST: jobs leave the queue ordered by planned slack time at s1."""

    def test_dispatches_lowest_planned_slack_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)

        rule = planned_slack_time(allowance=1.0)
        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # PST at s1 = (d - now) - sum(p_k + allowance for k from s1 onward),
        # allowance = 1, now ~= 100, routing [a, b]: pst = d - now - (a+1) - (b+1).
        # A: 200 - 100 - 3 - 4 = 93, B: 150 - 100 - 2 - 2 = 46, C: 300 - 100 - 5 - 5 = 190.
        # Ascending: B < A < C. Gaps are drift-invariant (every PST decreases by the same dt).
        for sku, due_date, pts in [
            ("A", 200.0, [2.0, 3.0]),
            ("B", 150.0, [1.0, 1.0]),
            ("C", 300.0, [4.0, 4.0]),
        ]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, s2],
                processing_times=pts,
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["B", "A", "C"]


class TestSlackPerRemainingOperationIntegration:
    """S/OPN = PST / remaining-op count: the division reorders vs plain PST."""

    def test_dispatches_lowest_slack_per_operation_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)

        rule = slack_per_remaining_operation(allowance=0.0)
        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # S/OPN = pst / |R_i|, allowance = 0, now ~= 100. pst = (d - now) - sum(pts from s1).
        #   A: |R|=1, [s1] p=5,        pst = 185-100-5  = 80,  /1 -> 80
        #   B: |R|=2, [s1,s2] p=5,5,   pst = 230-100-10 = 120, /2 -> 60
        #   C: |R|=2, [s1,s2] p=5,5,   pst = 350-100-10 = 240, /2 -> 120
        # S/OPN ascending: B(60) < A(80) < C(120) -> ["B", "A", "C"].
        # Plain PST (no division) would be A(80) < B(120) < C(240) -> ["A", "B", "C"], so
        # the A/B swap is the division signal; and the result differs from arrival order
        # ["A","B","C"], so a constant rule (falling back to arrival order) also fails it.
        # Crossings are far outside the run window (B<A holds until now=140), so it is robust.
        for sku, servers, pts, due_date in [
            ("A", [s1], [5.0], 185.0),
            ("B", [s1, s2], [5.0, 5.0], 230.0),
            ("C", [s1, s2], [5.0, 5.0], 350.0),
        ]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=servers,
                processing_times=pts,
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["B", "A", "C"]


class TestDynamicPriorityRefreshIntegration:
    """A job can overtake another mid-run because sort_queue refreshes at dispatch."""

    def test_critical_ratio_order_reflects_dispatch_time_not_arrival(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=1000.0,
            priority_policy=critical_ratio,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        # CR = (d - now) / pt. The two jobs' CR lines cross at now=40:
        #   EARLY_FAVORITE: (400 - now) / 40   -> at now~0: 10.0,  at now~100: 7.5
        #   LATE_FAVORITE:  (130 - now) / 10   -> at now~0: 13.0,  at now~100: 3.0
        # Frozen-at-arrival keys (the pre-refresh bug) would serve EARLY_FAVORITE first
        # (it is added first AND has the lower CR at t~0). But sort_queue re-evaluates at
        # dispatch (now~100), where LATE_FAVORITE has the lower CR, so it goes first.
        jobs: list[ProductionJob] = []
        for sku, due_date, pt in [("EARLY_FAVORITE", 400.0, 40.0), ("LATE_FAVORITE", 130.0, 10.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[pt],
                due_date=due_date,
                priority_policy=critical_ratio,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["LATE_FAVORITE", "EARLY_FAVORITE"]


class TestApparentTardinessCostIntegration:
    """ATC: jobs leave the contended queue highest-apparent-cost first."""

    def test_dispatches_highest_apparent_cost_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        server = Server(env=env, capacity=1, shopfloor=sf)
        rule = apparent_tardiness_cost(lookahead=2.0, avg_processing=5.0)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[server],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # At dispatch now ~= 100, all p=5, fixed p_bar=5, k=2 (denom 10). Equal p means
        # ATC orders by slack = d - 5 - now: smaller slack -> larger index -> served first.
        #   A: 300-5-100 = 195   B: 200-5-100 = 95   C: 140-5-100 = 35
        # Index order (urgent first): C > B > A -> ["C", "B", "A"]. Gaps (60, 100) dwarf the
        # ~5-unit drift per dispatch, and the order differs from arrival ["A","B","C"].
        for sku, due_date in [("A", 300.0), ("B", 200.0), ("C", 140.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[server],
                processing_times=[5.0],
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, server) == ["C", "B", "A"]


class TestCostOverTimeIntegration:
    """COVERT: jobs leave the contended queue highest-expected-cost first."""

    def test_dispatches_highest_cost_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        rule = cost_over_time(lookahead=4.0)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Routing [s1, s2], p=[5,5] -> RPT=10, denom k*RPT=40. At dispatch now ~= 100,
        # slack = d - now - 10, cost = max(0, 1 - slack/40)/5. Smaller slack -> higher cost.
        #   A: slack 35 -> cost (1-35/40)/5 = 0.025
        #   B: slack 20 -> cost (1-20/40)/5 = 0.100
        #   C: slack  5 -> cost (1- 5/40)/5 = 0.175
        # Cost order (urgent first): C > B > A -> ["C", "B", "A"]; differs from arrival.
        for sku, due_date in [("A", 145.0), ("B", 130.0), ("C", 115.0)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, s2],
                processing_times=[5.0, 5.0],
                due_date=due_date,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["C", "B", "A"]


class TestWorkInNextQueueIntegration:
    """WINQ: jobs whose next machine has the least queued work go first.

    Three candidates share the contended queue at s1 but route onward to three
    DIFFERENT next machines (s2, s3, s4), each pre-loaded with a different
    amount of queued work. WINQ depends only on that downstream load, so it is
    the sole discriminator — a rule that ignored it would fall back to arrival
    order and fail.
    """

    def test_dispatches_least_next_queue_work_first(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        s4 = Server(env=env, capacity=1, shopfloor=sf)

        # Long downstream blockers (>> 100) keep s2/s3/s4 busy past the s1 dispatch,
        # so their queued work stays intact while the candidates are scored.
        for downstream in (s2, s3, s4):
            db = ProductionJob(
                env=env,
                sku="DBLOCK",
                servers=[downstream],
                processing_times=[1000.0],
                due_date=10_000.0,
            )
            sf.add(db)
        env.run(until=ARRIVAL_STEP)

        # Pre-load each downstream queue with a distinct work content: s2->3, s3->9, s4->6.
        for downstream, work in [(s2, 3.0), (s3, 9.0), (s4, 6.0)]:
            filler = ProductionJob(
                env=env,
                sku=f"FILL{int(work)}",
                servers=[downstream],
                processing_times=[work],
                due_date=10_000.0,
            )
            sf.add(filler)
        env.run(until=env.now + ARRIVAL_STEP)

        # Block s1 so the candidates pile up behind it.
        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=work_in_next_queue,
        )
        sf.add(blocker)
        env.run(until=env.now + ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Candidates arrive A, B, C; their next machines carry work 3, 9, 6.
        # WINQ ascending: A(3) < C(6) < B(9) -> ["A", "C", "B"]; differs from arrival order.
        for sku, nxt in [("A", s2), ("B", s3), ("C", s4)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, nxt],
                processing_times=[5.0, 5.0],
                due_date=10_000.0,
                priority_policy=work_in_next_queue,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["A", "C", "B"]


class TestRaghuRajendranIntegration:
    """RR: with PT and slack held equal across candidates, WINQ breaks the tie.

    Same multi-server fixture as WINQ, but scored by RR with fixed u=0 and
    identical p / due date / routing length, so exp(u)*p and (s/RPT)*exp(-u)*p
    are equal across candidates and the WINQ term alone sets the order. This
    proves RR actually incorporates the next-queue look-ahead.
    """

    def test_winq_term_breaks_ties_by_next_queue_work(self) -> None:
        env = Environment()
        sf = ShopFloor(env=env)
        s1 = Server(env=env, capacity=1, shopfloor=sf)
        s2 = Server(env=env, capacity=1, shopfloor=sf)
        s3 = Server(env=env, capacity=1, shopfloor=sf)
        s4 = Server(env=env, capacity=1, shopfloor=sf)
        rule = raghu_rajendran(utilization=0.0)

        for downstream in (s2, s3, s4):
            db = ProductionJob(
                env=env,
                sku="DBLOCK",
                servers=[downstream],
                processing_times=[1000.0],
                due_date=10_000.0,
            )
            sf.add(db)
        env.run(until=ARRIVAL_STEP)

        for downstream, work in [(s2, 3.0), (s3, 9.0), (s4, 6.0)]:
            filler = ProductionJob(
                env=env,
                sku=f"FILL{int(work)}",
                servers=[downstream],
                processing_times=[work],
                due_date=10_000.0,
            )
            sf.add(filler)
        env.run(until=env.now + ARRIVAL_STEP)

        blocker = ProductionJob(
            env=env,
            sku="BLOCK",
            servers=[s1],
            processing_times=[BLOCKER_PROCESSING_TIME],
            due_date=10_000.0,
            priority_policy=rule,
        )
        sf.add(blocker)
        env.run(until=env.now + ARRIVAL_STEP)

        jobs: list[ProductionJob] = []
        # Identical p=[5,5] (RPT=10) and due_date=500 across candidates -> equal exp(u)*p and
        # (s/RPT)*exp(-u)*p terms; only WINQ differs (3, 9, 6). Z order: A(3) < C(6) < B(9).
        for sku, nxt in [("A", s2), ("B", s3), ("C", s4)]:
            job = ProductionJob(
                env=env,
                sku=sku,
                servers=[s1, nxt],
                processing_times=[5.0, 5.0],
                due_date=500.0,
                priority_policy=rule,
            )
            sf.add(job)
            env.run(until=env.now + ARRIVAL_STEP)
            jobs.append(job)

        env.run()
        assert _processed_order(jobs, s1) == ["A", "C", "B"]
