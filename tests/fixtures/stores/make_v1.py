r"""Generate the committed version-1 run store fixture (``tests/fixtures/stores/v1``).

The fixture must come from real 0.16 code paths, so run this script against
Simulatte 0.16.x, never against a checkout with store schema 2. From the
repository root, with a v0.16.0 worktree and the old fixture removed:

    git worktree add /tmp/simulatte-0.16 v0.16.0
    uv run --directory /tmp/simulatte-0.16 --all-extras python -I \
        "$PWD/tests/fixtures/stores/make_v1.py" "$PWD/tests/fixtures/stores/v1"

It prepares a tiny project through the headless coordinator (one inspection
request), submits one execution request (one committed result whose ready
INITIAL binds one presentation view) and copies the checkpointed
``index.sqlite`` plus the referenced ``results/<attempt>/`` directory.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

MODEL = """from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
import simulatte
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import DomainEvent, Deltas, event_type
class Cell(Entity, kind="fixture_cell"):
    state_schema: ClassVar[StateSchema] = StateSchema({"count": FieldSpec("int")})
    def __init__(self, env):
        self.count = 0
        env.entities.attach(self, name="cell")
@event_type("fixture.changed", touches={"fixture_cell": ("count",)})
class Changed(DomainEvent):
    cell: str
    count: int
@dataclass(frozen=True)
class Params:
    n: int = 2
@simulatte.model(params=Params, horizon=2, inputs=["input.txt"])
def build(env, layout, params):
    cell = Cell(env)
    value = params.n + int(Path("input.txt").read_text())
    def process():
        yield env.timeout(1)
        cell.count = value
        env.emit(Changed(cell=cell.id, count=value, deltas=Deltas.build().set(cell.id,"count",value).done()))
        (env.artifacts_dir / "answer.txt").write_text(str(value))
    env.process(process())
"""


def main(output: Path) -> None:
    from simulatte.execution.coordinator import Coordinator
    from simulatte.execution.snapshot import discover_project
    from simulatte.execution.store import RunStore

    if RunStore.schema_version != 1:
        raise SystemExit("Run this generator against Simulatte 0.16.x (store schema 1)")
    if output.exists():
        raise SystemExit(f"{output} exists; remove it first")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text('[tool.simulatte.bundle]\ninclude = ["plant.py"]\n')
        (root / "plant.py").write_text(MODEL)
        (root / "input.txt").write_text("5")
        with Coordinator(discover_project(root / "plant.py", root)) as coordinator:
            prepared = coordinator.prepare(timeout=30)
            request = coordinator.make_request(prepared, parameters={"n": 3})
            key = coordinator.submit(request, prepared.revision, timeout=30)
            result = coordinator.wait(key, timeout=60)
        store = root / ".simulatte"
        with closing(sqlite3.connect(store / "index.sqlite")) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("requests", "results", "views")
            }
        if counts != {"requests": 2, "results": 1, "views": 1}:
            raise SystemExit(f"Unexpected fixture contents: {counts}")
        output.mkdir(parents=True)
        shutil.copyfile(store / "index.sqlite", output / "index.sqlite")
        with closing(sqlite3.connect(output / "index.sqlite")) as db:
            db.execute("VACUUM")
        shutil.copytree(result.path, output / "results" / key.attempt_id)
    print(f"wrote {output} (attempt {key.attempt_id})")


if __name__ == "__main__":
    main(Path(sys.argv[1]).absolute())
