"""Command-line entry point; optional server libraries are imported only by view and studio."""

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
    from simulatte.execution.command import add_arguments, add_experiment_arguments, add_store_arguments

    run = commands.add_parser("run", help="Execute or reproduce a captured local model")
    add_arguments(run)
    studio = commands.add_parser("studio", help="Run and edit a local model in Studio")
    add_arguments(studio, headless=False)
    studio.add_argument("--port", type=int, default=0)
    studio.add_argument("--no-browser", action="store_true")
    add_experiment_arguments(commands.add_parser("experiment", help="Run and manage experiments headlessly"))
    add_store_arguments(commands.add_parser("store", help="Maintain the local run store"))
    worker = commands.add_parser("worker", help="Start a framed execution supervisor")
    worker.add_argument("--stdio", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        from simulatte.execution.command import run as execute

        return execute(args)
    if args.command in ("experiment", "store"):
        from simulatte.execution import command

        return command.experiment(args) if args.command == "experiment" else command.store(args)
    if args.command == "worker":
        from simulatte.execution.supervisor import Supervisor

        return Supervisor().run_stdio()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        if args.command == "studio":
            from simulatte.studio.command import serve_studio

            serve_studio(args)
            return 0
        from simulatte.studio.server import serve
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except ImportError:
        print('Studio requires optional dependencies: pip install "simulatte[studio]"', file=sys.stderr)
        return 2
    try:
        serve(args.trace, port=args.port, no_browser=args.no_browser)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0
