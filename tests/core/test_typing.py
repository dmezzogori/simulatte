"""Tests for typing module to ensure type aliases are importable."""

from __future__ import annotations


def test_type_aliases_importable() -> None:
    """All type aliases and structured types from typing module should be importable."""
    from simulatte.typing import (
        Builder,
        BuiltSystem,
        ProcessGenerator,
    )

    # Just verify they're importable and are the expected types
    assert Builder is not None
    assert ProcessGenerator is not None
    assert BuiltSystem is not None


def test_built_system_named_fields_and_unpacking() -> None:
    """BuiltSystem is a NamedTuple: named access, positional indexing, and unpacking all work."""
    from simulatte.builders import build_immediate_release_system
    from simulatte.environment import Environment

    result = build_immediate_release_system(env=Environment())

    # Named-field access.
    assert result.psp is None
    assert result.policy is None
    assert result.router is not None
    # Positional indexing is preserved (NamedTuple).
    assert result[0] is None  # psp
    assert result[2] is result.shop_floor
    assert result[4] is None  # policy
    # Five-target tuple unpacking still works.
    psp, servers, shop_floor, router, policy = result
    assert psp is None
    assert servers is result.servers
    assert policy is None
