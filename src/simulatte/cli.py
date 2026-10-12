"""Command-line entry point; optional server libraries are imported only by view."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["_worker"], ["worker", "--stdio"]):
        from simulatte.execution.supervisor import Supervisor

        return Supervisor().run_stdio()
    parser = argparse.ArgumentParser(prog="simulatte")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="Open a recorded trace in the local viewer")
    view.add_argument("trace")
    view.add_argument("--port", type=int, default=0)
    view.add_argument("--no-browser", action="store_true")
    from simulatte.execution.command import add_arguments

    run = commands.add_parser("run", help="Execute or reproduce a captured local model")
    add_arguments(run)
    worker = commands.add_parser("worker", help="Start a framed execution supervisor")
    worker.add_argument("--stdio", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        from simulatte.execution.command import run as execute

        return execute(args)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        from simulatte.studio.server import serve
    except ImportError:
        print('Studio requires optional dependencies: pip install "simulatte[studio]"', file=sys.stderr)
        return 2
    try:
        serve(args.trace, port=args.port, no_browser=args.no_browser)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0
