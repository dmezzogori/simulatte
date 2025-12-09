from __future__ import annotations

from simulatte.environment import Environment as _Env

# Re-export simulatte's Environment singleton to keep a single global clock
Environment = _Env

__all__ = ["Environment"]
