from __future__ import annotations

from simulatte.intralogistics.sku import SKU


class TestSKU:
    def test_creation(self) -> None:
        sku = SKU(id="STEEL-01", weight=10.0, volume=0.5)
        assert sku.id == "STEEL-01"
        assert sku.weight == 10.0
        assert sku.volume == 0.5
        assert sku.attributes == ()

    def test_with_attributes(self) -> None:
        sku = SKU(id="FRAG-01", weight=1.0, volume=0.1, attributes=(("fragile", True), ("temp_class", "cold")))
        assert sku.get_attribute("fragile") is True
        assert sku.get_attribute("temp_class") == "cold"
        assert sku.get_attribute("missing") is None

    def test_get_attribute_with_default(self) -> None:
        sku = SKU(id="X", weight=1.0, volume=0.1)
        assert sku.get_attribute("missing", default=42) == 42

    def test_frozen(self) -> None:
        sku = SKU(id="X", weight=1.0, volume=0.1)
        import pytest

        with pytest.raises(AttributeError):
            sku.id = "Y"  # type: ignore[misc]

    def test_hashable(self) -> None:
        sku1 = SKU(id="A", weight=1.0, volume=0.1)
        sku2 = SKU(id="A", weight=1.0, volume=0.1)
        assert hash(sku1) == hash(sku2)
        assert sku1 == sku2
        assert len({sku1, sku2}) == 1

    def test_different_skus_not_equal(self) -> None:
        sku1 = SKU(id="A", weight=1.0, volume=0.1)
        sku2 = SKU(id="B", weight=1.0, volume=0.1)
        assert sku1 != sku2
