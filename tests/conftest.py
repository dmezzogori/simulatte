"""Pytest fixtures for simulatte tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from simulatte.environment import Environment


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "unchecked_bundles: do not build and check a layout bundle at every preparation (see check_bundles)"
    )


@pytest.fixture
def env() -> Environment:
    """Fresh simulation environment for tests."""
    return Environment()


@pytest.fixture(autouse=True)
def check_bundles(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Check every layout bundle the code builds, as the reader checks a saved one.

    Preparation does not build the bundle and the bundle does not validate itself: it is built on first read from data
    that preparation validated (spec §9 of ``specs/2026-11-23-layout-only-models-design.md``). In the tests, every
    bundle built on a read, and at the end of the test the bundle of every layout the test prepared (built again,
    without caching it on the result), must pass the checks of ``LayoutBundle.from_dict`` (every field), be equal to
    the bundle read back, and carry the hashes of its data (``LayoutBundle.check``). The auto layer that preparation
    took without checking it against the schema again must be the one a checked layer gives. Nothing is checked inside
    ``prepare()``, so an error that moved from preparation to the first read fails the test that expects it at
    ``prepare()``. Tests that count what preparation builds opt out with ``@pytest.mark.unchecked_bundles``.
    """
    if request.node.get_closest_marker("unchecked_bundles") is not None:
        yield
        return
    from simulatte.layout import bundle as module
    from simulatte.layout.layers import LayerBundle
    from simulatte.layout.layout import Layout

    build = module._build_bundle
    finalize = Layout._finalize
    prepared = []

    def checked(resolved):
        layers = resolved.values.bundle
        assert LayerBundle((), layers.file_layer, resolved.auto.data).auto_layer == layers.auto_layer
        built = build(resolved)
        read = module.LayoutBundle.from_dict(built.to_dict())
        assert read == built
        read.check()
        return built

    def finalized(layout, coordinator):
        result = finalize(layout, coordinator)
        prepared.append(result)
        return result

    monkeypatch.setattr(module, "_build_bundle", checked)
    monkeypatch.setattr(Layout, "_finalize", finalized)
    yield
    for result in prepared:
        checked(result)
