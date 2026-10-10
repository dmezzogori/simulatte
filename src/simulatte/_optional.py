"""Lazy loading for optional integrations, with actionable missing-extra errors."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def import_optional(module: str, extra: str) -> ModuleType:
    try:
        return import_module(module)
    except ModuleNotFoundError as error:
        # A broken transitive dependency is not the same as an uninstalled extra.
        if error.name != module.split(".")[0]:
            raise
        raise ImportError(
            f"{module} requires the '{extra}' extra; install it with: pip install 'simulatte[{extra}]'"
        ) from error
