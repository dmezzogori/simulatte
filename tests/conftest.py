"""Pytest fixtures for simulatte tests.

This module provides fixtures to manage test environment setup and teardown,
particularly for singleton and identifiable state cleanup between tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from simulatte.environment import Environment
from simulatte.utils import IdentifiableMixin

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def clear_global_state() -> Generator[None, Any]:
    """Clear singleton and identifiable state before and after each test.

    This fixture ensures test isolation by resetting IdentifiableMixin ID counters.

    The cleanup runs automatically before and after every test.
    """
    IdentifiableMixin.clear()
    yield
    IdentifiableMixin.clear()


@pytest.fixture
def env() -> Environment:
    """Fresh simulation environment for tests."""

    return Environment()
