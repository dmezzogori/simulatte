"""Read supported saved results without a writer, migration, or project imports."""

import hashlib
from pathlib import Path
import sqlite3
import stat

from simulatte.execution.project_store import history
from simulatte.execution.publication import PublishedResult
from simulatte.execution.schema import AttemptKey, _mapping
from simulatte.execution.store import _read
from simulatte.execution.snapshot import _path


def _file_problem(root: Path, location: str, length: int | None, sha256: str) -> str | None:
    """``missing``, ``unsafe`` (a symlink or not a regular file), ``changed`` (length or hash), or ``None``."""
    path = root
    try:
        for part in Path(location).parts:
            path = path / part
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                return "unsafe"
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(info.st_mode):
        return "unsafe"
    if length is not None and info.st_size != length:
        return "changed"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return None if digest.hexdigest() == sha256 else "changed"


class StoreReader:
    root: Path
    _db: sqlite3.Connection
    version: int

    @classmethod
    def open(cls, root: Path):
        if root.is_symlink():
            raise ValueError("Run store must not be a symlink")
        root = root.resolve()
        path = root / "index.sqlite"
        if path.is_symlink() or not path.is_file():
            raise ValueError("No readable run store index")
        reader = cls()
        reader.root = root
        # Immutable mode avoids sidecar creation for a quiescent copied store. A live
        # WAL requires existing shared memory; read-only SQLite then uses that index.
        wal = root / "index.sqlite-wal"
        live = wal.exists() and wal.stat().st_size > 0
        if live and not (root / "index.sqlite-shm").exists():
            raise ValueError("Store needs WAL recovery; close its writer and open with a compatible runtime")
        reader._db = sqlite3.connect(
            path.as_uri() + "?mode=ro" + ("" if live else "&immutable=1"), uri=True, check_same_thread=False
        )
        reader._db.row_factory = sqlite3.Row
        try:
            reader.version = reader._db.execute("PRAGMA user_version").fetchone()[0]
            if reader.version not in (2, 3, 4, 5):
                raise ValueError(f"Unsupported run store schema {reader.version}; use a compatible reader")
            reader._db.execute("PRAGMA query_only=ON")
        except BaseException:
            reader.close()
            raise
        return reader

    def history(self, model_id=None, *, after=None, limit=50):
        return history(self._db, self.version, model_id, after=after, limit=limit)

    def result(self, attempt_id):
        row = self._db.execute(
            """SELECT r.*,a.request_id,a.generation FROM results r
            JOIN attempts a ON a.id=r.attempt WHERE r.attempt=?""",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        key = AttemptKey(row["request_id"], row["attempt"], row["generation"])
        return PublishedResult(
            key,
            self.root / "results" / key.attempt_id,
            _mapping(_read(row["manifest"])),
            _mapping(_read(row["fingerprint"])),
        )

    def artifacts(self, attempt_id):
        self.result(attempt_id)
        return tuple(
            dict(row)
            for row in self._db.execute(
                "SELECT path,length,sha256 FROM artifacts WHERE attempt=? ORDER BY path", (attempt_id,)
            )
        )

    def artifact(self, attempt_id, name):
        _path(name)
        if not any(item["path"] == name for item in self.artifacts(attempt_id)):
            raise KeyError(name)
        root = self.result(attempt_id).path / "artifacts"
        target = root / name
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise ValueError("Artifact escapes saved result")
        return target

    def check(self):
        """Every indexed file that is missing, unsafe or changed, by owner, name and recorded SHA-256.

        Covers result traces (``trace_sha256`` of the result manifest), result artifacts and published
        analysis artifacts. A file is matched only by its recorded hash: one with the same name but other
        bytes is ``changed``, never adopted. Returns problems sorted by kind, owner and name.
        """
        from simulatte.analysis import codec

        expected = []
        for attempt, manifest in self._db.execute("SELECT attempt,manifest FROM results"):
            trace = _read(manifest).get("trace_sha256")
            if isinstance(trace, str):
                expected.append(("result", attempt, "run.simtrace", f"results/{attempt}/run.simtrace", None, trace))
        for attempt, name, length, sha256 in self._db.execute("SELECT attempt,path,length,sha256 FROM artifacts"):
            expected.append(("result", attempt, name, f"results/{attempt}/artifacts/{name}", length, sha256))
        if self.version >= 4:
            for execution, manifest in self._db.execute("SELECT execution,manifest FROM analysis_outputs"):
                for output in codec.loads(manifest)["outputs"]:
                    for item in output["artifacts"]:
                        location = f"analysis-results/{execution}/{item['path']}"
                        expected.append(("analysis", execution, item["path"], location, item["length"], item["sha256"]))
        problems = []
        for kind, owner, name, location, length, sha256 in sorted(expected):
            problem = _file_problem(self.root, location, length, sha256)
            if problem is not None:
                problems.append(
                    {"kind": kind, "id": owner, "name": name, "path": location, "sha256": sha256, "problem": problem}
                )
        return problems

    def close(self):
        self._db.close()

    def generations(self):
        if self.version < 3:
            return ()
        return tuple(
            {**dict(row), "evidence": _read(row["evidence"])}
            for row in self._db.execute("SELECT * FROM environment_generations")
        )

    def experiment(self, experiment_id: str):
        from simulatte.execution.experiment_reads import experiment

        return experiment(self, experiment_id)

    def experiment_run(self, run_id: str):
        from simulatte.execution.experiment_reads import experiment_run

        return experiment_run(self, run_id)

    def replications(self, run_id: str, *, status: str | None = None):
        from simulatte.execution.experiment_reads import replications

        return replications(self, run_id, status=status)

    def replication_kpis(self, run_id: str):
        from simulatte.execution.experiment_reads import replication_kpis

        return replication_kpis(self, run_id)

    def _transaction(self):
        from contextlib import nullcontext

        return nullcontext(self._db)

    def _require_active(self, db, key):
        from simulatte.execution.store import StaleAttempt

        raise StaleAttempt("Recovery exposes committed results only")
