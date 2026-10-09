from __future__ import annotations

import random
from fractions import Fraction
from itertools import accumulate

import pytest

from simulatte.builders import build_immediate_release_system, build_lumscor_system
from simulatte.distributions import (
    Exponential,
    FlowShopRouting,
    GeneralFlowShopRouting,
    PureJobShopRouting,
    Uniform,
    general_flow_shop_routing,
    pure_flow_shop_routing,
    pure_job_shop_routing,
)
from simulatte.environment import Environment
from simulatte.events import Event, LogEvent
from simulatte.psp import PreShopPool
from simulatte.rng import RNG_DERIVATION, derive_seed
from simulatte.router import Router
from simulatte.runner import Runner
from simulatte.scenario import Scenario, ShopType, SkuFamily
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.typing import BuiltSystem


def _stream(seed: int, name: str) -> random.Random:
    """An independent replica of stream `name` of an environment seeded with `seed`."""
    return random.Random(derive_seed(seed, name))


def _floor(env: Environment, n: int) -> tuple[ShopFloor, list[Server]]:
    sf = ShopFloor(env=env)
    return sf, [Server(env=env, capacity=1, shopfloor=sf) for _ in range(n)]


# --- streams ---------------------------------------------------------------------------------------------


def test_derive_seed_pinned() -> None:
    assert RNG_DERIVATION == "simulatte-rng-v1"
    # Computed once from the formula in the spec (§8.1) and pinned: a change here breaks every recorded run.
    assert derive_seed(42, "router-1/interarrival") == 5344421528549109773708903139933152256
    assert derive_seed(0, "") == 194009465555999244889336481416486230531
    env = Environment(seed=42)
    assert env.rng("router-1/interarrival").random() == random.Random(5344421528549109773708903139933152256).random()


def test_streams_independent_and_reproducible() -> None:
    env = Environment(seed=7)
    a = env.rng("a")
    assert env.rng("a") is a  # cached per name
    first_a = [a.random() for _ in range(5)]

    # The same seed and name reproduce the stream in another environment.
    other = Environment(seed=7)
    assert [other.rng("a").random() for _ in range(5)] == first_a

    # Draws from one stream do not move another: interleaving changes nothing.
    interleaved = Environment(seed=7)
    values_a, values_b = [], []
    for _ in range(5):
        values_a.append(interleaved.rng("a").random())
        values_b.append(interleaved.rng("b").random())
    assert values_a == first_a
    assert values_b == [other.rng("b").random() for _ in range(5)]
    assert values_a != values_b  # different names, different streams

    # A different seed gives a different stream for the same name.
    assert [Environment(seed=8).rng("a").random() for _ in range(5)] != first_a

    with pytest.raises(TypeError, match="stream name"):
        env.rng(1)  # ty: ignore[invalid-argument-type]


def test_seed_range_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Environment(seed=0).seed == 0
    assert Environment(seed=2**63 - 1).seed == 2**63 - 1
    for bad in (-1, 2**63):
        with pytest.raises(ValueError, match="seed"):
            Environment(seed=bad)
    with pytest.raises(TypeError):
        Environment(seed=1.5)  # ty: ignore[invalid-argument-type]

    seed = Environment().seed
    assert 0 <= seed < 2**63

    monkeypatch.setattr("os.urandom", lambda n: b"\xff" * n)
    assert Environment().seed == 2**63 - 1  # int.from_bytes(os.urandom(8), "big") >> 1
    monkeypatch.setattr("os.urandom", lambda n: bytes(range(1, n + 1)))
    assert Environment().seed == int.from_bytes(bytes(range(1, 9)), "big") >> 1


def test_debug_streams_draw_the_same_values() -> None:
    def draws(env: Environment) -> list[object]:
        rng = env.rng("s")
        return [
            rng.random(),
            rng.randint(1, 6),
            rng.sample(range(10), k=3),
            rng.choices("abc", weights=(1, 2, 3), k=2),
            rng.gammavariate(2, 0.5),
            rng.expovariate(2.0),
            rng.uniform(30, 45),
        ]

    assert draws(Environment(seed=3, debug=True)) == draws(Environment(seed=3))


def test_debug_rejects_subscriber_drawing_from_rng() -> None:
    env = Environment(seed=1, debug=True)
    stream = env.rng("model")
    env.bus.subscribe(lambda event: stream.random(), "**")
    with pytest.raises(RuntimeError, match="drew from env.rng"):
        env.emit(LogEvent(level="INFO", message="hello"))
    assert not env.bus.delivering

    # A stream created inside the subscriber is counted too.
    env2 = Environment(seed=1, debug=True)
    env2.bus.subscribe(lambda event: env2.rng("fresh").randint(1, 3), "**")
    with pytest.raises(RuntimeError, match="drew from env.rng"):
        env2.emit(LogEvent(level="INFO", message="hello"))

    # Draws outside delivery are allowed, and outside debug mode nothing is checked.
    stream.random()
    plain = Environment(seed=1)
    seen: list[Event] = []
    plain.bus.subscribe(lambda event: seen.append(event) or plain.rng("model").random(), "**")
    plain.emit(LogEvent(level="INFO", message="hello"))
    assert len(seen) == 1


# --- binding ---------------------------------------------------------------------------------------------


def test_bind_scalar_routing_contextual_forms() -> None:
    env = Environment(seed=11)
    _, servers = _floor(env, 4)

    # scalar: number, description, opaque callable
    constant = env.bind(2, kind="scalar", stream="o/c", owner="o")
    assert constant() == 2.0 and isinstance(constant(), float)
    assert env.bind(Fraction(1, 2), kind="scalar", stream="o/h", owner="o")() == 0.5  # any real number
    sampler = env.bind(Exponential(2.0), kind="scalar", stream="o/e", owner="o")
    replica = Exponential(2.0).sampler(_stream(11, "o/e"))
    assert [sampler() for _ in range(5)] == [replica() for _ in range(5)]
    assert env.opaque_sampler_owners == []

    def opaque() -> float:
        return 1.0

    assert env.bind(opaque, kind="scalar", stream="p/x", owner="p") is opaque
    assert env.opaque_sampler_owners == ["p"]

    # routing: description, fixed sequence, opaque callable
    routing = env.bind(PureJobShopRouting(servers), kind="routing", stream="o/r", owner="o")
    replica_routing = PureJobShopRouting(servers).sampler(_stream(11, "o/r"))
    assert [routing() for _ in range(5)] == [replica_routing() for _ in range(5)]
    fixed = env.bind([servers[2], servers[0]], kind="routing", stream="o/f", owner="o")
    assert fixed() == (servers[2], servers[0]) and fixed() is fixed()

    def custom_routing() -> list[Server]:
        return servers[:1]

    assert env.bind(custom_routing, kind="routing", stream="q/r", owner="q") is custom_routing

    # contextual: description and number ignore the context; an opaque callable keeps its signature
    pick = env.bind(Uniform(1.0, 2.0), kind="contextual", stream="w/pick", owner="w")
    replica_pick = Uniform(1.0, 2.0).sampler(_stream(11, "w/pick"))
    assert [pick("sku", 3) for _ in range(3)] == [replica_pick() for _ in range(3)]
    put = env.bind(1.5, kind="contextual", stream="w/put", owner="w")
    assert put("sku", 3) == 1.5 and put() == 1.5

    def recharge(level: float, target: float) -> float:
        return target - level

    assert env.bind(recharge, kind="contextual", stream="c/recharge", owner="c") is recharge

    # owners are recorded once, in order of first opaque binding
    env.bind(lambda: 2.0, kind="scalar", stream="p/y", owner="p")
    assert env.opaque_sampler_owners == ["p", "q", "c"]

    # invalid kinds and values
    with pytest.raises(ValueError, match="kind"):
        env.bind(1.0, kind="vector", stream="o/z", owner="o")  # ty: ignore[no-matching-overload]
    for kind, value in (("scalar", "1.0"), ("scalar", True), ("routing", 3.0), ("routing", "abc"), ("contextual", [])):
        with pytest.raises(TypeError, match="cannot bind"):
            env.bind(value, kind=kind, stream="o/z", owner="o")
    with pytest.raises(TypeError, match="cannot bind"):
        env.bind(Exponential, kind="scalar", stream="o/z", owner="o")  # a class, not a description


def test_debug_rejects_two_values_on_one_stream() -> None:
    env = Environment(seed=3, debug=True)
    shared = Uniform(1.0, 2.0)
    env.bind(shared, kind="scalar", stream="o/s", owner="o")
    env.bind(shared, kind="scalar", stream="o/s", owner="o")  # the same value again is not a conflict
    env.bind(Uniform(1.0, 2.0), kind="scalar", stream="o/s", owner="o")  # an equal description neither
    env.bind(2.5, kind="contextual", stream="o/n", owner="o")
    with pytest.raises(ValueError, match="stream 'o/s' is already bound to a different value"):
        env.bind(Uniform(1.0, 3.0), kind="scalar", stream="o/s", owner="o")
    with pytest.raises(ValueError, match="stream 'o/n' is already bound"):
        env.bind(2.6, kind="contextual", stream="o/n", owner="o")
    with pytest.raises(ValueError, match="stream 'o/s' is already bound"):
        env.bind(lambda: 1.0, kind="scalar", stream="o/s", owner="p")
    assert env.opaque_sampler_owners == []  # a rejected binding records nothing

    # Outside debug mode the check is off.
    plain = Environment(seed=3)
    plain.bind(1.0, kind="scalar", stream="o/s", owner="o")
    plain.bind(2.0, kind="scalar", stream="o/s", owner="o")


def test_shared_description_independent_samplers() -> None:
    env = Environment(seed=5)
    shared = Exponential(1.0)
    first = env.bind(shared, kind="scalar", stream="o/one", owner="o")
    second = env.bind(shared, kind="scalar", stream="o/two", owner="o")
    one, two = [first() for _ in range(5)], [second() for _ in range(5)]
    assert one != two

    # A Router binds one sampler per (sku, server) stream even when the description is shared.
    env = Environment(seed=5)
    sf, servers = _floor(env, 2)
    psp = PreShopPool(env=env, shopfloor=sf)
    service = Uniform(1.0, 9.0)
    router = Router(
        env=env,
        shopfloor=sf,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=1.0,
        sku_distributions={"A": 1.0},
        sku_routings={"A": servers},
        sku_service_times={"A": dict.fromkeys(servers, service)},
        due_date_offset_distribution={"A": 30.0},
    )
    env.run(until=5.5)
    jobs = list(psp.jobs)
    assert len(jobs) == 5
    for position, server in enumerate(servers):
        replica = service.sampler(_stream(5, f"{router.id}/service/A/{server.id}"))
        assert [job.processing_times[position] for job in jobs] == [replica() for _ in jobs]
    assert [job.processing_times[0] for job in jobs] != [job.processing_times[1] for job in jobs]
    assert env.opaque_sampler_owners == []


def test_router_stream_names() -> None:
    seed = 21
    env = Environment(seed=seed)
    sf, servers = _floor(env, 3)
    psp = PreShopPool(env=env, shopfloor=sf)
    arrivals, due, service = Exponential(2.0), Uniform(30.0, 45.0), Uniform(1.0, 2.0)
    routings = {"A": GeneralFlowShopRouting(servers), "B": PureJobShopRouting(servers)}
    router = Router(
        env=env,
        shopfloor=sf,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=arrivals,
        sku_distributions={"A": 2.0, "B": 1.0},
        sku_routings=routings,
        sku_service_times={sku: dict.fromkeys(servers, service) for sku in ("A", "B")},
        due_date_offset_distribution={"A": due, "B": due},
    )
    env.run(until=20.0)
    jobs = list(psp.jobs)
    assert len(jobs) > 10
    rid = router.id

    interarrival = arrivals.sampler(_stream(seed, f"{rid}/interarrival"))
    assert [job.created_at for job in jobs] == pytest.approx(list(accumulate(interarrival() for _ in jobs)))
    sku_stream = _stream(seed, f"{rid}/sku")
    assert [job.sku for job in jobs] == [sku_stream.choices(("A", "B"), weights=(2.0, 1.0))[0] for _ in jobs]
    assert {job.sku for job in jobs} == {"A", "B"}

    for sku in ("A", "B"):
        of_sku = [job for job in jobs if job.sku == sku]
        routing = routings[sku].sampler(_stream(seed, f"{rid}/routing/{sku}"))
        assert [job.servers for job in of_sku] == [tuple(routing()) for _ in of_sku]
        offset = due.sampler(_stream(seed, f"{rid}/due/{sku}"))
        assert [job.due_date - job.created_at for job in of_sku] == pytest.approx([offset() for _ in of_sku])

    # One service-time stream per (sku, server), drawn once per visit in routing order.
    samplers = {
        (sku, server): service.sampler(_stream(seed, f"{rid}/service/{sku}/{server.id}"))
        for sku in ("A", "B")
        for server in servers
    }
    for job in jobs:
        assert job.processing_times == tuple(samplers[job.sku, server]() for server in job.servers)


# --- routing descriptions --------------------------------------------------------------------------------


def test_routing_factories_return_descriptions() -> None:
    env = Environment()
    _, servers = _floor(env, 3)
    assert pure_job_shop_routing(servers) == PureJobShopRouting(tuple(servers))
    assert general_flow_shop_routing(servers) == GeneralFlowShopRouting(tuple(servers))
    assert pure_flow_shop_routing(servers) == FlowShopRouting(tuple(servers))
    assert PureJobShopRouting(servers).servers == tuple(servers)  # frozen to a tuple
    with pytest.raises(ValueError, match="at least one server"):
        PureJobShopRouting(())


@pytest.mark.parametrize("shop_type", list(ShopType))
def test_all_three_shop_types_route(shop_type: ShopType) -> None:
    seed = 13
    with Environment(seed=seed) as env:
        scenario = Scenario(shop_type=shop_type)
        sf, servers = scenario.build_floor(env)
        psp = PreShopPool(env=env, shopfloor=sf)  # keep every arrival for inspection
        router = scenario.build_router(env, sf, servers, psp=psp)
        env.run(until=60.0)
    jobs = list(psp.jobs)
    assert len(jobs) > 30
    assert env.opaque_sampler_owners == []  # a Scenario binds only managed forms

    description = {
        ShopType.PJS: PureJobShopRouting,
        ShopType.GFS: GeneralFlowShopRouting,
        ShopType.PFS: FlowShopRouting,
    }[shop_type](servers)
    replica = description.sampler(_stream(seed, f"{router.id}/routing/F1"))
    assert [job.servers for job in jobs] == [tuple(replica()) for _ in jobs]

    index = {server: i for i, server in enumerate(servers)}
    lengths = {len(job.servers) for job in jobs}
    for job in jobs:
        positions = [index[s] for s in job.servers]
        assert len(set(positions)) == len(positions)  # no re-entry
        if shop_type is ShopType.PFS:
            assert positions == list(range(len(servers)))
        elif shop_type is ShopType.GFS:
            assert positions == sorted(positions)
    if shop_type is ShopType.PJS:
        assert any([index[s] for s in job.servers] != sorted(index[s] for s in job.servers) for job in jobs)
    if shop_type is not ShopType.PFS:
        assert len(lengths) > 1  # random routing length


def test_legacy_custom_routing_callable_is_opaque() -> None:
    env = Environment(seed=2)
    sf, servers = _floor(env, 2)
    psp = PreShopPool(env=env, shopfloor=sf)
    router = Router(
        env=env,
        shopfloor=sf,
        servers=servers,
        psp=psp,
        inter_arrival_distribution=1.0,
        sku_distributions={"A": 1.0},
        sku_routings={"A": lambda: [servers[1], servers[0]]},
        sku_service_times={"A": dict.fromkeys(servers, 2.0)},
        due_date_offset_distribution={"A": 10.0},
    )
    env.run(until=3.5)
    assert [job.servers for job in psp.jobs] == [(servers[1], servers[0])] * 3
    assert env.opaque_sampler_owners == [router.id]

    # A custom SkuFamily routing factory may still return a plain callable (opaque) ...
    def legacy_factory(pool):
        return lambda: list(pool)[::-1]

    with Environment(seed=2) as env:
        scenario = Scenario(families=(SkuFamily(routing_factory=legacy_factory, expected_routing_length=6.0),))
        sf, servers = scenario.build_floor(env)
        router = scenario.build_router(env, sf, servers, psp=None)
        env.run(until=20.0)
        assert env.opaque_sampler_owners == [router.id]
        assert all(job.servers == tuple(servers[::-1]) for job in sf.jobs_done)

    # ... or a description, which is managed.
    with Environment(seed=2) as env:
        scenario = Scenario(families=(SkuFamily(routing_factory=GeneralFlowShopRouting, expected_routing_length=3.5),))
        sf, servers = scenario.build_floor(env)
        scenario.build_router(env, sf, servers, psp=None)
        assert env.opaque_sampler_owners == []


# --- global random and Runner ----------------------------------------------------------------------------


def test_library_never_touches_global_random(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the library used the global random module")

    # The module-level functions are bound methods of the hidden global instance.
    patched = [name for name in dir(random) if getattr(getattr(random, name), "__self__", None) is random._inst]
    assert {"random", "seed", "choices", "expovariate", "gammavariate", "randint", "sample"} <= set(patched)
    for name in patched:
        monkeypatch.setattr(random, name, forbidden)

    for shop_type in ShopType:
        with Environment(seed=1) as env:
            system = build_immediate_release_system(env=env, scenario=Scenario(shop_type=shop_type))
            env.run(until=100.0)
            assert system.shop_floor.jobs_done
    with Environment(seed=1) as env:
        system = build_lumscor_system(env=env, check_timeout=10.0, wl_norm_level=6.0, allowance_factor=2)
        env.run(until=100.0)
        assert system.shop_floor.jobs_done
    assert Runner(builder=_immediate, seeds=[1, 2], extract_fn=_summary, progress=False).run(until=100.0)


def _immediate(*, env: Environment) -> BuiltSystem[None]:
    return build_immediate_release_system(env=env)


def _summary(system: BuiltSystem[None]) -> tuple[int, float, tuple[float, ...]]:
    done = system.shop_floor.jobs_done
    return len(done), system.shop_floor.average_time_in_system, tuple(job.due_date for job in done)


def test_runner_parallel_equals_sequential() -> None:
    seeds = [3, 1, 4]
    sequential = Runner(builder=_immediate, seeds=seeds, extract_fn=_summary, progress=False).run(until=200.0)
    parallel = Runner(builder=_immediate, seeds=seeds, extract_fn=_summary, parallel=True, n_jobs=2, progress=False)
    assert parallel.run(until=200.0) == sequential
    assert len(set(sequential)) == len(seeds)  # different seeds, different runs
    for seed, result in zip(seeds, sequential, strict=True):
        with Environment(seed=seed) as env:
            system = _immediate(env=env)
            env.run(until=200.0)
            assert _summary(system) == result  # Runner(seed=s) is Environment(seed=s)
