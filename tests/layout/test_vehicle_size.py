"""A vehicle's size in its placement and in the bundle (studio map spec §6.1, §6.5)."""

from __future__ import annotations

import hashlib
import inspect
import json
import math

import pytest

from simulatte import Environment
from simulatte.builders import build_immediate_release_system
from simulatte.intralogistics.agv import AGV, AGVType
from simulatte.intralogistics.builders import build_simple_system
from simulatte.intralogistics.speed import TrapezoidalProfile
from simulatte.layout import LayoutBundle, LayoutError
from simulatte.layout.auto import TargetMetadata, auto_layout
from simulatte.layout.bundle import reresolve_presentation
from simulatte.layout.identity import hashes
from simulatte.layout.layers import FileLayer, resolve_layers
from simulatte.layout.topology import StaticTopology
from simulatte.scenario import Scenario


def kind(**size):
    return AGVType("cart", TrapezoidalProfile(2, 1, 1), 100, 100, 100, **size)


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def simple(length=None, width=None):
    env = Environment(seed=1)
    build_simple_system(env, agv_length=length)
    if width is not None:
        for entity in env.entities.live():
            if isinstance(entity, AGV):
                entity.agv_type = kind(length=length, width=width)
    return env.layout.prepare().bundle


def production():
    env = Environment(seed=3)
    build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=3))
    return env.layout.prepare().bundle


def hash_triple(bundle):
    return (bundle.physical_hash, bundle.full_hash, bundle.layers_hash)


# --- AGVType.width and the resolved size ------------------------------------------------------------------------


@pytest.mark.parametrize("width", [-0.1, math.nan, math.inf, -math.inf])
def test_width_is_validated_like_length(width):
    with pytest.raises(ValueError, match="width must be finite and >= 0"):
        kind(length=1.0, width=width)
    with pytest.raises(ValueError, match="length must be finite and >= 0"):
        kind(length=width)


def test_width_is_keyword_only_and_the_positional_order_is_unchanged():
    profile, depletion, recharge = TrapezoidalProfile(2, 1, 1), lambda d, w, s: d, lambda b, t: t
    positional = AGVType("cart", profile, 100, 200, 300, bool, depletion, recharge, 0.3, 0.1, 2.0, 3.0, 1.5, 0.25)
    assert (positional.length, positional.min_gap, positional.width) == (1.5, 0.25, None)
    assert (positional.load_time, positional.unload_time) == (2.0, 3.0)
    assert inspect.signature(AGVType).parameters["width"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        AGVType("cart", profile, 100, 200, 300, bool, None, None, 0.2, 0.05, 0.0, 0.0, 1.5, 0.25, None, None, 0.0, 1.0)


@pytest.mark.parametrize(
    ("length", "width", "size"),
    [
        (2.0, 1.0, (2.0, 1.0)),
        (1.2, None, (1.2, 0.6)),
        (1.2, 0.0, (1.2, 0.6)),  # a width of 0 is not declared: half the length
        (None, None, None),
        (None, 0.5, None),  # no length, no size
        (0.0, None, None),  # a length of 0 is not declared
        (0.0, 0.5, None),
        (5e-324, None, (5e-324, 5e-324)),  # half of it is 0.0: the length itself
    ],
)
def test_the_size_of_a_type(length, width, size):
    assert kind(length=length, width=width).size == size


# --- the footprint and the target ---------------------------------------------------------------------------------


def footprint(bundle, name="vehicle-0"):
    return dict(bundle["resolved"]["placements"][name]["footprint"])


def test_length_and_width_give_the_footprint_and_the_target_size():
    bundle = simple(2.0, 1.0)
    assert footprint(bundle) == {"width": 2.0, "depth": 1.0, "height": 0.0}
    assert bundle["targets"]["vehicle-0"]["size"] == (2.0, 1.0)
    assert bundle["auto"]["targets"]["vehicle-0"]["size"] == (2.0, 1.0)
    assert bundle["algorithms"]["auto"] == bundle["auto"]["algorithm"] == "layered-v2"
    bundle.check()
    assert LayoutBundle.from_dict(bundle.to_dict()) == bundle


def test_a_length_alone_gives_a_depth_of_half_the_length():
    bundle = simple(1.2)
    assert footprint(bundle) == {"width": 1.2, "depth": 0.6, "height": 0.0}
    assert bundle["targets"]["vehicle-1"]["size"] == (1.2, 0.6)


@pytest.mark.parametrize(("length", "width"), [(None, None), (0.0, None), (0.0, 2.0)])
def test_no_length_or_a_length_of_0_keeps_1_by_1_and_writes_no_size(length, width):
    bundle = simple(length, width)
    assert footprint(bundle) == {"width": 1.0, "depth": 1.0, "height": 0.0}
    assert "size" not in bundle["targets"]["vehicle-0"]
    assert bundle["algorithms"]["auto"] == bundle["auto"]["algorithm"] == "layered-v1"


def test_a_width_of_0_with_a_length_gives_half_the_length():
    bundle = simple(1.0, 0.0)
    assert footprint(bundle) == {"width": 1.0, "depth": 0.5, "height": 0.0}
    assert bundle["targets"]["vehicle-0"]["size"] == (1.0, 0.5)


def test_the_smallest_length_still_prepares():
    bundle = simple(5e-324)
    assert footprint(bundle) == {"width": 5e-324, "depth": 5e-324, "height": 0.0}


def test_an_explicit_footprint_wins():
    targets = {"cart": TargetMetadata("agv", size=(2.0, 1.0))}
    explicit = resolve_layers(
        (),
        FileLayer({"schema_version": "1.0", "placements": {"cart": {"kind": "agv", "footprint": {"depth": 3}}}}),
        targets={"cart": "agv"},
    )
    auto = auto_layout(targets, StaticTopology(), explicit)
    merged = resolve_layers((), explicit.bundle.file_layer, auto.data, targets={"cart": "agv"})
    assert dict(merged.data["placements"]["cart"]["footprint"]) == {"width": 2.0, "depth": 3.0, "height": 0.0}
    assert auto.algorithm == "layered-v2"


# --- target metadata ------------------------------------------------------------------------------------------------


def test_target_fields_round_trip_and_name_the_algorithm():
    sized = TargetMetadata("agv", size=(1, 0.5))
    bound = TargetMetadata("warehouse", node_ids=("A", "B"))
    assert sized.size == (1.0, 0.5) and type(sized.size[0]) is float
    assert sized.to_dict() == {"kind": "agv", "explicitly_placed": False, "size": [1.0, 0.5]}
    assert bound.to_dict() == {"kind": "warehouse", "explicitly_placed": False, "node_ids": ["A", "B"]}
    for item in (sized, bound):
        assert TargetMetadata.from_dict(json.loads(json.dumps(item.to_dict()))) == item
    for name in ("parking_area", "charging_station"):
        assert TargetMetadata(name, node_ids=("A",)).node_ids == ("A",)
    explicit = resolve_layers((), FileLayer({"schema_version": "1.0"}), targets={})
    assert auto_layout({"w": bound}, StaticTopology(), explicit).algorithm == "layered-v2"
    assert auto_layout({"w": TargetMetadata("warehouse")}, StaticTopology(), explicit).algorithm == "layered-v1"


@pytest.mark.parametrize(
    "metadata",
    [
        {"kind": "server", "size": (1.0, 1.0)},
        {"kind": "agv", "size": (1.0,)},
        {"kind": "agv", "size": (1.0, 1.0, 1.0)},
        {"kind": "agv", "size": (0.0, 1.0)},
        {"kind": "agv", "size": (1.0, -1.0)},
        {"kind": "agv", "size": (1.0, math.inf)},
        {"kind": "agv", "size": (True, 1.0)},
        {"kind": "agv", "size": "ab"},
        {"kind": "agv", "size": 1.0},
        {"kind": "agv", "node_ids": ("A",)},
        {"kind": "server", "node_ids": ("A",)},
        {"kind": "warehouse", "node_ids": ()},
        {"kind": "warehouse", "node_ids": ("",)},
        {"kind": "warehouse", "node_ids": (1,)},
        {"kind": "warehouse", "node_ids": "A"},
    ],
)
def test_invalid_size_and_node_ids(metadata):
    with pytest.raises(LayoutError):
        TargetMetadata(**metadata)


# --- what does not move ---------------------------------------------------------------------------------------------

# Read with the code of 7671694, before the vehicle size: the three hashes (physical, full, layers) and the sha256 of
# the whole bundle; then of the bundle reresolve_presentation gives with the label patches below.
PINNED_PRODUCTION = (
    "62dc0b29bf7c01916c231d7f1f0f69789c6f4d1f8f390cd0c026684ca16b8cd2",
    "cb7a094eb935c3cf5d5bd5ab1170e1af3d5417fc614602f8e89ba697ae19fa39",
    "a6ea603abed8fb556f2385949adb757ef40a17c805c7b90bdab636273f1ad13a",
)
PINNED_PRODUCTION_RERESOLVED = "42cf5c0939d701f3ca1e8f06eac329809c18cb05b8e4b1730b92cb031a0b532e"
PINNED_UNSIZED = (
    "d501def0ac7e5b3da241f2bdc15b086cbe5e3fb10bff5302369c0498f0b8cc9d",
    "f20569ceb37800a4e8d323a2b98f40261789845cc8d0d6facc8c5a9e56daa4f7",
    "dbee1fc6aa2c425a435ec4332130ce792cbefd148118216666f203e00669e0a9",
)
PINNED_UNSIZED_BUNDLE = "8c7638d75692c5e6130ca4b9a71bec334d4fbaf0437be7fc97b1b7a919a73cc9"
PINNED_UNSIZED_RERESOLVED = "7f7516f713bf148148ba601d0572d1a2f9f6957c405660c003339c24e074f427"


def test_a_production_only_model_keeps_layered_v1_its_hashes_and_its_reresolution():
    bundle = production()
    assert bundle["algorithms"]["auto"] == bundle["auto"]["algorithm"] == "layered-v1"
    assert hash_triple(bundle) == PINNED_PRODUCTION
    patch = {"schema_version": "1.0", "placements": {"wc-0": {"kind": "server", "visual": {"label": "Alpha"}}}}
    replay = reresolve_presentation(bundle, patch)
    assert replay.bundle is not None and sha(replay.bundle.to_dict()) == PINNED_PRODUCTION_RERESOLVED


def test_vehicles_without_a_length_keep_layered_v1_and_their_hashes():
    bundle = simple()
    assert bundle["algorithms"]["auto"] == bundle["auto"]["algorithm"] == "layered-v1"
    assert hash_triple(bundle) == PINNED_UNSIZED
    assert sha(bundle.to_dict()) == PINNED_UNSIZED_BUNDLE


def test_a_bundle_saved_before_the_size_reresolves_as_it_did():
    # Before the change a model with sized vehicles saved the very bundle of the same model without a length (its
    # sha256 was PINNED_UNSIZED_BUNDLE for both), so this is the bundle a released version saved for either model.
    saved = LayoutBundle.from_dict(json.loads(json.dumps(simple().to_dict())))
    assert sha(saved.to_dict()) == PINNED_UNSIZED_BUNDLE
    patch = {"schema_version": "1.0", "placements": {"WH-A": {"kind": "warehouse", "visual": {"label": "Alpha"}}}}
    replay = reresolve_presentation(saved, patch)
    assert replay.bundle is not None and sha(replay.bundle.to_dict()) == PINNED_UNSIZED_RERESOLVED
    assert replay.bundle["auto"]["algorithm"] == "layered-v1"


def test_a_sized_model_gets_layered_v2_and_new_hashes_and_reresolves_to_layered_v2():
    bundle = simple(1.2)
    assert bundle["algorithms"]["auto"] == "layered-v2"
    assert hash_triple(bundle) != PINNED_UNSIZED
    patch = {"schema_version": "1.0", "placements": {"WH-A": {"kind": "warehouse", "visual": {"label": "Alpha"}}}}
    replay = reresolve_presentation(bundle, patch)
    assert replay.bundle is not None
    assert replay.bundle["algorithms"]["auto"] == replay.bundle["auto"]["algorithm"] == "layered-v2"
    assert footprint(replay.bundle) == {"width": 1.2, "depth": 0.6, "height": 0.0}
    assert replay.bundle.physical_hash == bundle.physical_hash


# --- the reader -----------------------------------------------------------------------------------------------------


def rehashed(data):
    data.update(hashes(data))
    return data


def sized_data():
    return simple(1.2).to_dict()


def test_the_reader_accepts_both_names_and_node_ids():
    LayoutBundle.from_dict(simple().to_dict()).check()
    LayoutBundle.from_dict(sized_data()).check()
    data = simple().to_dict()
    for section in (data["targets"], data["auto"]["targets"]):
        section["WH-A"]["node_ids"] = ["WH_A_OUT"]
    data["algorithms"]["auto"] = data["auto"]["algorithm"] = "layered-v2"
    LayoutBundle.from_dict(rehashed(data)).check()


def _set(data, path, value):
    parent = data
    for key in path[:-1]:
        parent = parent[key]
    if value is KeyError:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("algorithms", "auto"), "layered-v3"),
        (("auto", "algorithm"), "layered-v3"),
        (("algorithms", "auto"), "layered-v1"),  # disagrees with auto.algorithm and with the sized targets
        (("auto", "algorithm"), "layered-v1"),
        (("targets", "vehicle-0", "size"), [1.2]),
        (("targets", "vehicle-0", "size"), [1.2, 0.0]),
        (("auto", "targets", "vehicle-0", "size"), "big"),
        (("targets", "WH-A", "size"), [1.0, 1.0]),
        (("targets", "vehicle-0", "node_ids"), ["N1"]),
        (("targets", "WH-A", "node_ids"), []),
        (("targets", "WH-A", "node_ids"), [7]),
        (("auto", "targets", "WH-A", "extent"), 1),
        (("targets", "vehicle-0", "size"), None),
        (("auto", "targets", "vehicle-0", "size"), None),
        (("targets", "WH-A", "node_ids"), None),
        (("auto", "targets", "WH-A", "node_ids"), None),
    ],
)
def test_the_reader_rejects_other_names_and_malformed_fields(path, value):
    data = sized_data()
    _set(data, path, value)
    with pytest.raises(LayoutError):
        LayoutBundle.from_dict(rehashed(data))


def test_the_reader_rejects_a_layered_v2_name_without_the_fields_and_the_fields_under_layered_v1():
    data = simple().to_dict()
    data["algorithms"]["auto"] = data["auto"]["algorithm"] = "layered-v2"
    with pytest.raises(LayoutError, match="algorithm"):
        LayoutBundle.from_dict(rehashed(data))
    data = sized_data()
    data["algorithms"]["auto"] = data["auto"]["algorithm"] = "layered-v1"
    with pytest.raises(LayoutError, match="algorithm"):
        LayoutBundle.from_dict(rehashed(data))
    data = sized_data()
    del data["auto"]["targets"]["vehicle-0"]["size"], data["auto"]["targets"]["vehicle-1"]["size"]
    with pytest.raises(LayoutError, match="algorithm"):
        LayoutBundle.from_dict(rehashed(data))
    data = sized_data()  # the auto layer's targets still carry the size, the saved targets do not
    del data["targets"]["vehicle-0"]["size"], data["targets"]["vehicle-1"]["size"]
    with pytest.raises(LayoutError, match="algorithm"):
        LayoutBundle.from_dict(rehashed(data))
