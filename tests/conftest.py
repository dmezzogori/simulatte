"""Pytest fixtures for simulatte tests.

This module provides fixtures to manage test environment setup and teardown,
particularly for singleton and identifiable state cleanup between tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from simulatte.utils import IdentifiableMixin
from simulatte.utils.singleton import Singleton

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def clear_global_state() -> Generator[None, Any, None]:
    """Clear singleton and identifiable state before and after each test.

    This fixture ensures test isolation by:
    1. Clearing Singleton instances (including Environment)
    2. Resetting IdentifiableMixin ID counters

    The cleanup runs automatically before and after every test.
    """
    Singleton.clear()
    IdentifiableMixin.clear()
    yield
    Singleton.clear()
    IdentifiableMixin.clear()
