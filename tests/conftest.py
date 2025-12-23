"""Pytest fixtures for simulatte tests."""

from __future__ import annotations

import pytest

from simulatte.environment import Environment


@pytest.fixture
def env() -> Environment:
    """Fresh simulation environment for tests."""
    return Environment()
