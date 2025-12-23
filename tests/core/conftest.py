from __future__ import annotations

import pytest

from simulatte.environment import Environment


@pytest.fixture
def env() -> Environment:
    """Provide a fresh Environment for each test."""
    return Environment()
