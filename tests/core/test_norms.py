from __future__ import annotations

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.policies.norms import expand_norms, fits_norms
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor


def _make_system() -> tuple[Environment, ShopFloor]:
    env = Environment()
    sf = ShopFloor(env=env)
    return env, sf


# ---------- fits_norms boundary semantics ----------


def test_fits_norms_exactly_at_norm_releases() -> None:
    """A contribution landing exactly at the norm fits (the check is <=, not <)."""
    env, sf = _make_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=10.0)

    # wip + PT/(0+1) = 5.0 + 5.0 == norm 10.0 → fits.
    assert fits_norms(job, wip={server: 5.0}, norms={server: 10.0}) is True


def test_fits_norms_epsilon_below_norm_blocks() -> None:
    """The same setup with the norm reduced by a small epsilon does not fit."""
    env, sf = _make_system()
    server = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[server], processing_times=[5.0], due_date=10.0)

    # wip + PT = 10.0 > norm 10.0 - 1e-9 → blocked.
    assert fits_norms(job, wip={server: 5.0}, norms={server: 10.0 - 1e-9}) is False


def test_fits_norms_position_discounts_downstream_contribution() -> None:
    """A multi-operation job contributes PT/(i+1): the second operation counts half.

    Routing s1(PT=2.0) -> s2(PT=4.0): contributions are 2.0/1 = 2.0 at s1 and
    4.0/2 = 2.0 at s2. With norms of 3.0 each, the undiscounted second PT (4.0)
    would exceed its norm, but the position-corrected contribution fits.
    Servers absent from wip count as 0.0.
    """
    env, sf = _make_system()
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[2.0, 4.0], due_date=30.0)

    assert fits_norms(job, wip={}, norms={s1: 3.0, s2: 3.0}) is True


def test_fits_norms_second_position_exact_boundary() -> None:
    """The <= boundary also holds at a downstream position: wip + PT/2 == norm fits."""
    env, sf = _make_system()
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)
    job = ProductionJob(env=env, sku="A", servers=[s1, s2], processing_times=[1.0, 4.0], due_date=30.0)

    # At s2: wip 1.0 + 4.0/2 = 3.0 == norm 3.0 → fits; epsilon less → blocked.
    assert fits_norms(job, wip={s2: 1.0}, norms={s1: 10.0, s2: 3.0}) is True
    assert fits_norms(job, wip={s2: 1.0}, norms={s1: 10.0, s2: 3.0 - 1e-9}) is False


# ---------- expand_norms scalar expansion ----------


def test_expand_norms_scalar_expands_to_all_servers() -> None:
    """A scalar norm expands to a float-valued entry for every shopfloor server."""
    env, sf = _make_system()
    s1 = Server(env=env, capacity=1, shopfloor=sf)
    s2 = Server(env=env, capacity=1, shopfloor=sf)

    assert expand_norms(shopfloor=sf, wl_norm=5) == {s1: 5.0, s2: 5.0}
