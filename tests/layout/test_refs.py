import json
from typing import Any
import pytest
from simulatte.layout import Address, GraphRef, NodeRef, PortRef, ValueRef, LayoutNotReady


def test_owner_identity_and_wire_addresses():
    owner = object()
    address = Address({"kind": "lattice", "i": -1, "j": 2})
    a, b = NodeRef(owner, address), NodeRef(object(), address)
    assert a != b
    assert a == NodeRef(owner, address)
    assert a.to_dict() == address.to_dict()
    assert hash(a) != hash(b)
    for ref in (a, PortRef(owner, "a", "out"), GraphRef(owner), ValueRef(owner, ("placements", "a", "position"))):
        with pytest.raises(LayoutNotReady):
            _ = ref.x
        assert "owner" not in ref.to_dict()
    assert PortRef(owner, "a", "out").address.to_dict() == {"kind": "port", "entity": "a", "port": "out"}


def test_address_encoding_is_injective_and_copied():
    values: list[dict[str, Any]] = [{"kind": "explicit", "id": x} for x in ("a:b", "a/b", "a-1", "warehouse-2", "è")]
    values += [{"kind": "lattice", "i": -1, "j": 1}, {"kind": "lattice", "i": 1, "j": -1}]
    addresses = [Address(value) for value in values]
    ids = [address.encoded_id for address in addresses]
    assert len(set(ids)) == len(ids)
    for address, encoded in zip(addresses, ids):
        assert "/" not in encoded and "-" not in encoded and "\0" not in encoded
        assert json.loads(bytes.fromhex(encoded.removeprefix("layout:"))) == address.to_dict()
    values[0]["id"] = "changed"
    assert addresses[0].to_dict()["id"] == "a:b"


@pytest.mark.parametrize(
    "factory",
    [
        lambda owner: NodeRef(owner, Address({"kind": "explicit", "id": "a"})),
        lambda owner: PortRef(owner, "a", "p"),
        lambda owner: ValueRef(owner, ("grid",)),
        lambda owner: GraphRef(owner),
    ],
)
def test_references_are_frozen(factory):
    ref = factory(object())
    with pytest.raises(AttributeError):
        ref.accidental = "mutable"


def test_intersection_address_sorts_incidents():
    a = {"kind": "lane_segment", "lane": "a", "source": "1", "target": "2"}
    b = {"kind": "lane_vertex", "lane": "b", "vertex": "3"}
    assert Address({"kind": "intersection", "incidents": [b, a]}) == Address(
        {"kind": "intersection", "incidents": [a, b]}
    )


def test_node_ref_rejects_non_node_address():
    from simulatte.layout import LayoutError

    with pytest.raises(LayoutError):
        NodeRef(object(), Address({"kind": "graph"}))


def test_owner_comparison_uses_identity_and_refs_have_no_arbitrary_attributes():
    class Token:
        def __eq__(self, other):
            return True

    owner = Token()
    address = Address({"kind": "explicit", "id": "a"})
    assert NodeRef(owner, address) != NodeRef(Token(), address)
    assert NodeRef(owner, address) != address
    assert ValueRef(owner, ("grid", "dx")).path == ("grid", "dx")
    with pytest.raises(AttributeError):
        _ = GraphRef(owner).missing


def test_diagnostics_copy_paths_and_validate_severity():
    from simulatte.layout import LayoutDiagnostic, LayoutError

    paths = [["grid", "dx"]]
    diagnostic = LayoutDiagnostic("schema", "error", ["grid"], None, None, "bad grid", paths)  # ty: ignore[invalid-argument-type]  # Runtime accepts/copies caller sequences; invalid severity tested.
    paths[0][0] = "changed"
    assert diagnostic.path == ("grid",)
    assert diagnostic.related_paths == (("grid", "dx"),)
    error = LayoutError("invalid", diagnostics=[diagnostic])  # ty: ignore[invalid-argument-type]  # Runtime accepts/copies caller sequences; invalid severity tested.
    assert error.diagnostics == (diagnostic,)
    with pytest.raises(ValueError):
        LayoutDiagnostic("schema", "invalid", (), None, None, "bad severity")  # ty: ignore[invalid-argument-type]  # Runtime accepts/copies caller sequences; invalid severity tested.
