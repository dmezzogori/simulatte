from __future__ import annotations


from simulatte.agv import AGVKind
from simulatte.controllers.agvs_controller import AGVController
from simulatte.policies.agv_selection_policy.idle_feeding_selection_policy import IdleFeedingSelectionPolicy
from simulatte.policies.agv_selection_policy.reverse_feeding_selection_policy import ReverseFeedingSelectionPolicy
from simulatte.policies.agv_selection_policy.workload_selection_policy import WorkloadAGVSelectionPolicy


class DummyMission:
    def __init__(self, start_time: float | None):
        self.start_time = start_time
        self.request = type("Req", (), {"time": start_time or 0})()


class DummyAGV:
    def __init__(self, *, kind: AGVKind, n_users: int = 0, n_queue: int = 0, start_time: float | None = None):
        self.kind = kind
        self.n_users = n_users
        self.n_queue = n_queue
        self.current_mission = DummyMission(start_time) if start_time is not None else None
        self.missions = []
        self.idle_time = 10
        self.waiting_time = 5
        self._travel_time = 2
        self.picking_cell = type("Cell", (), {"__name__": "DummyCell"})


def test_workload_agv_selection_policy_chooses_least_busy():
    agv_busy = DummyAGV(kind=AGVKind.FEEDING, n_users=2, n_queue=3)
    agv_free = DummyAGV(kind=AGVKind.FEEDING, n_users=0, n_queue=1)

    policy = WorkloadAGVSelectionPolicy()
    chosen = policy(agvs=[agv_busy, agv_free])
    assert chosen is agv_free

    chosen_excluding = policy(agvs=[agv_busy, agv_free], exceptions=[agv_free])
    assert chosen_excluding is agv_busy


def test_idle_feeding_selection_policy_sorts_by_load_and_timestamp():
    agv_a = DummyAGV(kind=AGVKind.FEEDING, n_users=1, n_queue=1, start_time=5)
    agv_b = DummyAGV(kind=AGVKind.FEEDING, n_users=0, n_queue=2, start_time=1)
    agv_c = DummyAGV(kind=AGVKind.FEEDING, n_users=0, n_queue=1, start_time=None)

    policy = IdleFeedingSelectionPolicy()
    ordered = policy(agvs=[agv_a, agv_b, agv_c])
    assert ordered[0] is agv_c  # fewest users/queue
    assert ordered[-1] is agv_a

    # Exceptions are removed
    ordered_filtered = policy(agvs=[agv_a, agv_b], exceptions=[agv_a])
    assert ordered_filtered == [agv_b]


def test_reverse_feeding_selection_policy_reverses_and_filters():
    agvs = [DummyAGV(kind=AGVKind.FEEDING) for _ in range(3)]
    reversed_agvs = ReverseFeedingSelectionPolicy()(agvs=agvs)
    assert tuple(reversed(agvs)) == reversed_agvs

    filtered = ReverseFeedingSelectionPolicy()(agvs=agvs, exceptions=[agvs[0]])
    assert agvs[0] not in filtered


def test_agv_controller_groups_and_best_selection(capsys):
    agvs = [
        DummyAGV(kind=AGVKind.FEEDING),
        DummyAGV(kind=AGVKind.REPLENISHMENT),
        DummyAGV(kind=AGVKind.INPUT),
    ]

    class FirstPolicy:
        def __call__(self, *, agvs, exceptions=None):
            return next(iter(agvs))

    controller = AGVController(agvs=agvs, agv_selection_policy=FirstPolicy())

    assert controller.best_feeding_agv() is agvs[0]
    assert controller.best_replenishment_agv() is agvs[1]
    assert controller.best_input_agv() is agvs[2]

    missions = list(controller.agvs_missions())
    assert missions == []

    controller.summary()
    out = capsys.readouterr().out
    assert "Performance Summary" in out
