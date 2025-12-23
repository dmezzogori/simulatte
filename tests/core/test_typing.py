"""Tests for typing module to ensure type aliases are importable."""

from __future__ import annotations


def test_type_aliases_importable() -> None:
    """All type aliases from typing module should be importable."""
    from simulatte.typing import (
        Builder,
        ProcessGenerator,
        PullSystem,
        PushSystem,
        System,
    )

    # Just verify they're importable and are the expected types
    assert Builder is not None
    assert ProcessGenerator is not None
    assert PullSystem is not None
    assert PushSystem is not None
    assert System is not None
