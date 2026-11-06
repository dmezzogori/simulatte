"""Check every cancelled/failed prefix against the still-running installed Studio."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main(args):
    access = json.loads(args.access.read_text())
    observed = json.loads(args.browser.read_text())["observed"]
    headers = {
        "X-Simulatte-Session": access["secret"],
        "Cookie": f"{access['cookie_name']}={access['cookie']}",
    }
    rows = []
    for attempt, status in observed.items():
        if status == "succeeded":
            continue
        try:
            with urlopen(Request(f"{access['origin']}/api/prefix/{attempt}/1", headers=headers), timeout=10):
                raise AssertionError("Revoked attempt still readable")
        except HTTPError as error:
            assert error.code == 410
            error.close()
        rows.append(dict(attempt=attempt, status=status, http=410))
    args.output.write_text(json.dumps(dict(passed=True, mode="live_http", rows=rows), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
