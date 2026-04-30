from __future__ import annotations

import pytest

from simulatte.environment import Environment
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.graph import Arc, LayoutGraph, Node
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.intralogistics.traffic import FreeTrafficManager, PathCheckResult, ResourceBasedTrafficManager


def _make_agv(env: Environment, node: Node | None = None) -> AGV:
    profile = TrapezoidalProfile(max_speed=2.0, acceleration=1.0, deceleration=1.0)
    agv_type = AGVType(
        name="test",
        speed_profile=profile,
        battery_capacity=100.0,
        weight_capacity=500.0,
        volume_capacity=2.0,
    )
    return AGV(env=env, agv_type=agv_type, initial_node=node)


class TestPathCheckResult:
    def test_feasible(self) -> None:
        r = PathCheckResult(feasible=True)
        assert r.feasible is True
        assert r.conflict_nodes is None

    def test_infeasible_with_conflicts(self) -> None:
        n = Node(id="X", x=0.0, y=0.0)
        r = PathCheckResult(feasible=False, conflict_nodes=[n])
        assert r.feasible is False
        assert r.conflict_nodes == [n]


class TestFreeTrafficManager:
    def test_check_path_always_feasible(self) -> None:
        tm = FreeTrafficManager()
        agv = _make_agv(Environment())
        result = tm.check_path(agv, [])
        assert result.feasible is True

    def test_place_is_noop(self) -> None:
        env = Environment()
        tm = FreeTrafficManager()
        node = Node(id="A", x=0.0, y=0.0)
        agv = _make_agv(env, node)

        def run():
            yield from tm.place(agv, node)

        env.process(run())
        env.run()

    def test_enter_leave_are_noops(self) -> None:
        env = Environment()
        tm = FreeTrafficManager()
        node = Node(id="A", x=0.0, y=0.0)
        agv = _make_agv(env, node)

        def run():
            yield from tm.enter_node(agv, node)
            tm.leave_node(agv, node)

        env.process(run())
        env.run()

    def test_register_intent_is_noop(self) -> None:
        tm = FreeTrafficManager()
        agv = _make_agv(Environment())
        node = Node(id="A", x=0.0, y=0.0)
        tm.register_intent(agv, [node])

    def test_cancel_is_noop(self) -> None:
        tm = FreeTrafficManager()
        agv = _make_agv(Environment())
        tm.cancel(agv)

    def test_no_priority_method(self) -> None:
        tm = FreeTrafficManager()
        assert not hasattr(tm, "priority")


class TestResourceBasedTrafficManager:
    def test_place_acquires_node(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        def run():
            yield from tm.place(agv, n1)

        env.process(run())
        env.run()
        assert tm._node_resources[n1].count == 1

    def test_enter_blocks_when_full(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n1)
        entered = []

        def occupy():
            yield from tm.place(agv1, n2)
            yield env.timeout(10.0)
            tm.leave_node(agv1, n2)

        def try_enter():
            yield from tm.enter_node(agv2, n2)
            entered.append(env.now)

        env.process(occupy())
        env.process(try_enter())
        env.run()

        assert entered[0] == pytest.approx(10.0)

    def test_register_intent_and_cancel(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        tm.register_intent(agv, [n1, n2])
        assert agv in tm._intents
        tm.cancel(agv)
        assert agv not in tm._intents

    def test_priority_uses_priority_fn(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, priority_fn=lambda agv: 7.5 if agv.agv_id else 0.0)
        agv = _make_agv(env, n1)
        assert tm.priority(agv) == 7.5

    def test_check_path_detects_head_on_conflict(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=2.0, y=0.0)
        graph = LayoutGraph(
            [n1, n2, n3],
            [
                Arc(source=n1, target=n2),
                Arc(source=n2, target=n3),
            ],
        )
        tm = ResourceBasedTrafficManager(graph=graph, env=env)

        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n3)

        tm.register_intent(agv1, [n1, n2, n3])
        result = tm.check_path(agv2, [n3, n2, n1])
        assert result.feasible is False
        assert result.conflict_nodes is not None
        assert n2 in result.conflict_nodes

    def test_check_path_detects_shared_destination(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=2.0, y=0.0)
        graph = LayoutGraph(
            [n1, n2, n3],
            [
                Arc(source=n1, target=n2),
                Arc(source=n3, target=n2),
            ],
        )
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n3)

        tm.register_intent(agv1, [n1, n2])
        result = tm.check_path(agv2, [n3, n2])
        assert result.feasible is False
        assert result.conflict_nodes is not None
        assert n2 in result.conflict_nodes

    def test_cancel_removes_pending_request(self) -> None:
        """Cancel should remove intents AND cancel pending SimPy requests."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv_blocker = _make_agv(env, n2)
        agv_waiter = _make_agv(env, n1)
        entered_after_cancel = []

        def block_then_release():
            yield from tm.place(agv_blocker, n2)
            yield env.timeout(20.0)
            tm.leave_node(agv_blocker, n2)

        def try_enter_then_get_cancelled():
            tm.register_intent(agv_waiter, [n1, n2])
            # Start entering — this will block because agv_blocker holds n2
            gen = tm.enter_node(agv_waiter, n2)
            yield from gen

        def cancel_after_delay():
            yield env.timeout(5.0)
            tm.cancel(agv_waiter)

        def late_entrant():
            # Another AGV tries to enter n2 after cancel — should succeed at t=20
            yield env.timeout(10.0)
            agv3 = _make_agv(env, n1)
            yield from tm.enter_node(agv3, n2)
            entered_after_cancel.append(env.now)

        env.process(block_then_release())
        env.process(try_enter_then_get_cancelled())
        env.process(cancel_after_delay())
        env.process(late_entrant())
        env.run()

        assert agv_waiter not in tm._intents
        assert agv_waiter not in tm._pending_requests
        # late_entrant should get in at t=20 when blocker leaves
        assert entered_after_cancel[0] == pytest.approx(20.0)

    def test_leave_node_updates_intent(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=2.0, y=0.0)
        graph = LayoutGraph(
            [n1, n2, n3],
            [
                Arc(source=n1, target=n2),
                Arc(source=n2, target=n3),
            ],
        )
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        tm.register_intent(agv, [n1, n2, n3])
        assert tm._intents[agv] == [n1, n2, n3]

        def move():
            yield from tm.place(agv, n1)
            yield from tm.enter_node(agv, n2)
            tm.leave_node(agv, n1)

        env.process(move())
        env.run()

        assert n1 not in tm._intents[agv]

    def test_check_path_no_conflict_different_paths(self) -> None:
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        n3 = Node(id="N3", x=0.0, y=1.0)
        n4 = Node(id="N4", x=1.0, y=1.0)
        graph = LayoutGraph(
            [n1, n2, n3, n4],
            [
                Arc(source=n1, target=n2),
                Arc(source=n3, target=n4),
            ],
        )
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv1 = _make_agv(env, n1)
        agv2 = _make_agv(env, n3)

        tm.register_intent(agv1, [n1, n2])
        result = tm.check_path(agv2, [n3, n4])
        assert result.feasible is True

    def test_check_path_short_path_is_feasible(self) -> None:
        """A path with fewer than 2 nodes has no future nodes, so it is always feasible."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)
        assert tm.check_path(agv, [n1]).feasible is True
        assert tm.check_path(agv, []).feasible is True

    def test_check_path_skips_own_intent(self) -> None:
        """An AGV's own intent must not conflict with itself."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)
        agv = _make_agv(env, n1)

        tm.register_intent(agv, [n1, n2])
        result = tm.check_path(agv, [n1, n2])
        assert result.feasible is True

    def test_enter_node_interrupt_key_already_cleaned(self) -> None:
        """Line 138: enter_node interrupted after key was already deleted
        from _node_requests (e.g. via cancel() before interrupt fires)."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv_blocker = _make_agv(env, n2)
        agv_waiter = _make_agv(env, n1)

        def block_forever():
            yield from tm.place(agv_blocker, n2)
            yield env.timeout(100.0)

        def enter_then_get_interrupted():
            # Start entering n2 - will block
            enter_proc = env.process(tm.enter_node(agv_waiter, n2))
            yield env.timeout(1.0)
            # Before interrupt: delete the key from _node_requests to simulate
            # cancel() cleaning up first
            key = (agv_waiter, n2)
            if key in tm._node_requests:
                del tm._node_requests[key]
            # Now interrupt the enter_node process
            if enter_proc.is_alive:
                enter_proc.interrupt("test")
            yield env.timeout(0.1)

        env.process(block_forever())
        env.process(enter_then_get_interrupted())
        env.run(until=5.0)

        # Should not raise — the interrupt handler handles missing key gracefully
        assert (agv_waiter, n2) not in tm._node_requests

    def test_cancel_stale_node_requests_not_triggered_not_processed(self) -> None:
        """Lines 185-186: cancel() with _node_requests entry that is not triggered
        and not processed, and NOT in _pending_requests."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv_blocker = _make_agv(env, n2)
        agv_stale = _make_agv(env, n1)

        def setup():
            # Blocker occupies n2
            yield from tm.place(agv_blocker, n2)

        env.process(setup())
        env.run()

        # Manually insert a stale request for agv_stale on n2 (pending, not processed)
        # This simulates a state where _pending_requests was already cleaned up
        # but _node_requests still has the entry
        resource = tm._node_resources[n2]
        req = resource.request()
        tm._node_requests[(agv_stale, n2)] = req
        # Verify it's not triggered (blocked by blocker)
        assert not req.triggered
        # Do NOT add to _pending_requests — simulating the stale cleanup path

        # Now cancel should clean up via the stale-keys loop
        tm.cancel(agv_stale)

        assert (agv_stale, n2) not in tm._node_requests
        assert agv_stale not in tm._pending_requests

    def test_cancel_pending_request_already_triggered(self) -> None:
        """Line 173->178: cancel() when pending_request is already triggered
        (already acquired the resource)."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        graph = LayoutGraph([n1], [])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)

        agv = _make_agv(env, n1)

        def setup():
            # Place AGV: request triggers immediately (capacity=2, no contention)
            yield from tm.place(agv, n1)
            # After placement, the request is triggered
            # Manually add it to _pending_requests to simulate the state
            req = tm._node_requests[(agv, n1)]
            tm._pending_requests[agv] = req
            assert req.triggered  # it's already triggered/acquired

        env.process(setup())
        env.run()

        # cancel() should handle the triggered pending request
        tm.cancel(agv)
        assert agv not in tm._pending_requests

    def test_cancel_preserves_triggered_node_requests(self) -> None:
        """cancel() clears intents/pending requests but does not release occupied nodes."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)

        agv = _make_agv(env, n1)

        def setup():
            # Place AGV at n1 (triggered, acquired)
            yield from tm.place(agv, n1)
            # Also enter n2 (triggered, acquired since capacity=2)
            yield from tm.enter_node(agv, n2)

        env.process(setup())
        env.run()

        # Now we have two _node_requests for agv: (agv, n1) and (agv, n2)
        # Both are triggered. _pending_requests should be empty (cleared after yield req)
        assert agv not in tm._pending_requests
        assert (agv, n1) in tm._node_requests
        assert (agv, n2) in tm._node_requests

        tm.cancel(agv)

        # Triggered requests represent physical occupancy and remain until leave_node().
        assert tm._node_resources[n1].count == 1
        assert tm._node_resources[n2].count == 1
        assert (agv, n1) in tm._node_requests
        assert (agv, n2) in tm._node_requests

        tm.leave_node(agv, n1)
        tm.leave_node(agv, n2)
        assert tm._node_resources[n1].count == 0
        assert tm._node_resources[n2].count == 0

    def test_cancel_releases_triggered_pending_for_stale_node(self) -> None:
        """cancel() releases a triggered pending request that maps to a node
        different from the AGV's current_node (lines 179-180)."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=2)

        agv = _make_agv(env, n1)

        def setup():
            yield from tm.place(agv, n1)
            yield from tm.enter_node(agv, n2)

        env.process(setup())
        env.run()

        # Both nodes occupied. Simulate a pending request that references n2
        # while current_node is n1 (stale next-node request after an interrupt).
        req = tm._node_requests[(agv, n2)]
        assert req.triggered
        tm._pending_requests[agv] = req
        agv.current_node = n1

        tm.cancel(agv)

        # n2 should be released (stale), n1 should be kept (current occupancy)
        assert agv not in tm._pending_requests
        assert (agv, n2) not in tm._node_requests

    def test_leave_node_without_node_request_entry(self) -> None:
        """Line 148->155: leave_node when there's no entry in _node_requests
        for the given (agv, node) pair."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env)

        agv = _make_agv(env, n1)
        tm.register_intent(agv, [n1, n2])

        # leave_node without any prior place/enter -> no key in _node_requests
        tm.leave_node(agv, n1)

        # Should not crash; intent for n1 should be removed
        assert n1 not in tm._intents[agv]

    def test_leave_node_cancels_untriggered_request(self) -> None:
        """leave_node on a pending (untriggered) request should cancel it, not release it."""
        env = Environment()
        n1 = Node(id="N1", x=0.0, y=0.0)
        n2 = Node(id="N2", x=1.0, y=0.0)
        graph = LayoutGraph([n1, n2], [Arc(source=n1, target=n2)])
        tm = ResourceBasedTrafficManager(graph=graph, env=env, node_capacity=1)

        agv_blocker = _make_agv(env, n2)
        agv_waiter = _make_agv(env, n1)

        def block_forever():
            yield from tm.place(agv_blocker, n2)
            yield env.timeout(100.0)

        def try_enter_then_leave():
            yield env.timeout(1.0)
            # Start entering — will block because blocker holds n2
            resource = tm._node_resources[n2]
            req = resource.request()
            tm._node_requests[(agv_waiter, n2)] = req
            # req is pending (not triggered) because n2 is full
            assert not req.triggered
            # leave_node should cancel the pending request safely
            tm.leave_node(agv_waiter, n2)
            assert (agv_waiter, n2) not in tm._node_requests
            # The resource queue should be empty after cancelling
            assert len(resource.queue) == 0

        env.process(block_forever())
        env.process(try_enter_then_leave())
        env.run(until=10.0)


class TestMinimalTrafficManager:
    """A TrafficManager with only the 6 core methods should satisfy the protocol."""

    def test_minimal_implementation_satisfies_protocol(self) -> None:
        from simulatte.intralogistics.traffic import TrafficManager

        class MinimalTM:
            def place(self, agv, node):
                return
                yield

            def check_path(self, agv, path):
                return PathCheckResult(feasible=True)

            def register_intent(self, agv, path):
                pass

            def enter_node(self, agv, node):
                return
                yield

            def leave_node(self, agv, node):
                pass

            def cancel(self, agv):
                pass

        assert isinstance(MinimalTM(), TrafficManager)
