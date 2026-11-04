"""Read retained attempt databases independently of the gate's own assertions."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def main(directory):
    meta = json.loads((directory / "metadata.json").read_text())
    recorded = json.loads((directory / "result.json").read_text())["rows"]
    database = Path(meta["workspace"]) / "project/.simulatte/index.sqlite"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        attempts = dict(db.execute("SELECT id,status FROM attempts"))
        published = {row[0] for row in db.execute("SELECT attempt FROM results")}
    good = {r["attempt"] for r in recorded if r["status"] == "succeeded"}
    bad = {r["attempt"] for r in recorded if r["status"] != "succeeded"}
    assert published == good and not (published & bad)
    assert all(attempts[r["attempt"]] == r["status"] for r in recorded)
    result = dict(
        passed=True,
        published=len(published),
        bad_results=len(published & bad),
        good_attempts=sorted(good),
        bad_attempts=sorted(bad),
        database=str(database),
        database_sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
        exporter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (directory / "store-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(passed=True, published=len(good), bad_results=0)))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    main(p.parse_args().directory)
