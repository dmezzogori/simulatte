from __future__ import annotations

import importlib


def test_jobs_module_importable():
    importlib.reload(importlib.import_module("simulatte.jobs"))
