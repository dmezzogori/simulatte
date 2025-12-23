"""Builders for common jobshop system configurations."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Literal

from simulatte.distributions import server_sampling, truncated_2erlang
from simulatte.environment import Environment
from simulatte.policies.lumscor import LumsCor, lumscor_starvation_trigger
from simulatte.policies.slar import Slar
from simulatte.policies.starvation_avoidance import starvation_avoidance_process
from simulatte.psp import PreShopPool
from simulatte.router import Router
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor

if TYPE_CHECKING:  # pragma: no cover
    from simulatte.typing import PushSystem, System


def build_push_system(
    env: Environment,
    n_servers: int,
    arrival_rate: float = 1.0,
    service_rate: float = 1.0,
) -> PushSystem:
    """Build a simple push system with explicit env injection."""
    shop_floor = ShopFloor(env=env)
    servers = tuple(Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(n_servers))
    router = Router(
        env=env,
        shopfloor=shop_floor,
        servers=servers,
        psp=None,
        inter_arrival_distribution=lambda: random.expovariate(arrival_rate),
        family_distributions={"F1": 1},
        family_routings={"F1": dict.fromkeys(servers, 1.0)},  # type: ignore[arg-type]
        family_service_times={
            "F1": {server: lambda: random.expovariate(service_rate) for server in servers},
        },
        waiting_time_distribution={"F1": lambda: random.expovariate(1.0 * n_servers)},
    )
    return None, servers, shop_floor, router


class LiteratureSystemBuilder:
    """Builder with literature-inspired defaults."""

    _n_servers: int = 6
    _arrival_rate: float = 1 / 0.648
    _service_rate: float = 2.0

    @staticmethod
    def __call__(
        env: Environment,
        system: Literal["push", "lumscor", "slar"],
        *,
        check_timeout: float | int | None = None,
        wl_norm_level: float | int | None = None,
        allowance_factor: float | int | None = None,
    ) -> System:
        if system == "push":
            return LiteratureSystemBuilder.build_system_push(env)
        if system == "lumscor":
            assert check_timeout is not None and wl_norm_level is not None and allowance_factor is not None
            return LiteratureSystemBuilder.build_system_lumscor(
                env,
                check_timeout=float(check_timeout),
                wl_norm_level=float(wl_norm_level),
                allowance_factor=float(allowance_factor),
            )
        if system == "slar":
            assert allowance_factor is not None
            return LiteratureSystemBuilder.build_system_slar(
                env,
                allowance_factor=float(allowance_factor),
            )
        msg = f"Unknown system type: {system}"
        raise ValueError(msg)

    @staticmethod
    def build_system_push(env: Environment) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=None,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            family_distributions={"F1": 1},
            family_routings={"F1": server_sampling(servers)},
            family_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        )
        return None, servers, shop_floor, router

    @staticmethod
    def build_system_lumscor(
        env: Environment,
        check_timeout: float | int,
        wl_norm_level: float | int,
        allowance_factor: float | int,
    ) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        psp = PreShopPool(
            env=env,
            shopfloor=shop_floor,
            check_timeout=float(check_timeout),
            psp_release_policy=LumsCor(
                shopfloor=shop_floor,
                wl_norm=dict.fromkeys(servers, float(wl_norm_level)),
                allowance_factor=int(allowance_factor),
            ),
        )
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            family_distributions={"F1": 1},
            family_routings={"F1": server_sampling(servers)},
            family_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
        )

        env.process(lumscor_starvation_trigger(shopfloor=shop_floor, psp=psp))
        env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

        return None, servers, shop_floor, router

    @staticmethod
    def build_system_slar(env: Environment, allowance_factor: float | int) -> System:
        shop_floor = ShopFloor(env=env)
        servers = tuple(
            Server(env=env, capacity=1, shopfloor=shop_floor) for _ in range(LiteratureSystemBuilder._n_servers)
        )
        psp = PreShopPool(env=env, shopfloor=shop_floor, check_timeout=0, psp_release_policy=None)
        slar = Slar(allowance_factor=int(allowance_factor))
        router = Router(
            env=env,
            shopfloor=shop_floor,
            servers=servers,
            psp=psp,
            inter_arrival_distribution=lambda: random.expovariate(LiteratureSystemBuilder._arrival_rate),
            family_distributions={"F1": 1},
            family_routings={"F1": server_sampling(servers)},
            family_service_times={
                "F1": {
                    server: lambda: truncated_2erlang(
                        lam=LiteratureSystemBuilder._service_rate,
                        max_value=4.0,
                    )
                    for server in servers
                },
            },
            waiting_time_distribution={"F1": lambda: random.uniform(30, 45)},  # noqa: S311
            priority_policies=lambda job, server: slar.pst_priority_policy(job, server) or 0.0,
        )
        env.process(slar.slar_release_triggers(shopfloor=shop_floor, psp=psp))
        env.process(starvation_avoidance_process(shop_floor, psp))  # type: ignore[arg-type]

        return None, servers, shop_floor, router
