"""Small ordinary-Python starters, installed without replacing existing files."""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import uuid4

from simulatte.execution.snapshot import _path, _validate_names

_PRODUCTION = """from dataclasses import dataclass
from simulatte import Environment, Layout, model
from simulatte.builders import build_immediate_release_system
from simulatte.collectors import ShopFloorKPIs
from simulatte.scenario import Scenario

@dataclass(frozen=True)
class Params:
    servers: int = 3

@model(params=Params, horizon=120)
def build(env, layout, params):
    system = build_immediate_release_system(env=env, scenario=Scenario.pure_job_shop(n_servers=params.servers))
    ShopFloorKPIs(system.shop_floor).attach(env)
"""
_LOGISTICS = """from dataclasses import dataclass
from simulatte import Environment, Layout, model
from simulatte.intralogistics import SKU, build_simple_system

@dataclass(frozen=True)
class Params:
    quantity: int = 5

@model(params=Params, horizon=120)
def build(env, layout, params):
    fleet, agvs, origin, destination, graph = build_simple_system(env)
    order = fleet.create_order(
        sku=SKU("A", weight=1.0, volume=0.1), quantity=params.quantity,
        origin=origin, destination=destination,
    )
    fleet.submit(order)
"""
_SCRIPT = """
if __name__ == "__main__":
    with Environment(seed=17) as env:
        build(env, Layout(env), Params())
        env.run(until=120)
    print("Completed simulation")
"""


def starter_files(kind: Literal["production", "logistics"]) -> dict[str, bytes]:
    if kind not in ("production", "logistics"):
        raise ValueError("Unknown starter kind")
    config = f'''[project]
name = "simulatte-{kind}-project"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["simulatte[studio]>=0.17.0"]

[tool.simulatte.bundle]
include = ["models/*.py"]
layouts = ["layouts/{kind}.json"]

[tool.simulatte.project]
schema = 1
id = "{uuid4()}"
default_model = "{kind}"

[tool.simulatte.models.{kind}]
id = "{uuid4()}"
name = "{kind.title()}"
entrypoint = "models/{kind}.py:build"
layout = "layouts/{kind}.json"
'''
    return {
        "pyproject.toml": config.encode(),
        f"models/{kind}.py": ((_PRODUCTION if kind == "production" else _LOGISTICS) + _SCRIPT).encode(),
    }


def install_starter(root: Path, files: Mapping[str, bytes]) -> None:
    root = root.resolve()
    _validate_names(files)
    for name in files:
        _path(name)
        target = root / name
        if not target.resolve().is_relative_to(root):
            raise ValueError("Starter path escapes project root")
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
    created = []
    directories = []
    try:
        for name, content in files.items():
            target = root / name
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                directories.append(directory)
            with target.open("xb") as stream:
                created.append(target)
                stream.write(content)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        for directory in reversed(directories):
            directory.rmdir()
        raise
