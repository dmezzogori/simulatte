"""Command-line entry point; optional server libraries are imported only by view and studio."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["_worker"], ["worker", "--stdio"]) or (len(argv) in (2, 4) and argv[0] == "_worker"):
        from simulatte.execution.schema import _hash
        from simulatte.execution.supervisor import Supervisor

        # A remote bootstrap passes the hash of the trusted runtime it was staged from, then
        # the host's cache root and the uv binary it found.
        runtime = argv[1] if argv[0] == "_worker" and len(argv) in (2, 4) else None
        cache, uv = argv[2:4] if len(argv) == 4 else (None, None)
        if runtime is not None:
            _hash(runtime)
        return Supervisor(trusted_runtime_hash=runtime, cache=cache, uv=uv).run_stdio()
    parser = argparse.ArgumentParser(prog="simulatte")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="Open a recorded trace in the local viewer")
    view.add_argument("trace")
    view.add_argument("--port", type=int, default=0)
    view.add_argument("--no-browser", action="store_true")
    from simulatte.execution.command import (
        add_arguments,
        add_experiment_arguments,
        add_replay_arguments,
        add_store_arguments,
    )

    run = commands.add_parser("run", help="Execute or reproduce a captured local model")
    add_arguments(run)
    studio = commands.add_parser("studio", help="Open a project folder in Studio (default: current folder)")
    add_arguments(studio, headless=False)
    studio.add_argument("--port", type=int, default=0)
    studio.add_argument("--no-browser", action="store_true")
    add_experiment_arguments(commands.add_parser("experiment", help="Run and manage experiments headlessly"))
    add_replay_arguments(commands.add_parser("replay", help="Replay a committed request at full level and verify it"))
    add_store_arguments(commands.add_parser("store", help="Maintain the local run store"))
    from simulatte.execution.workers_cli import add_workers_arguments

    add_workers_arguments(commands.add_parser("workers", help="Configure and check remote workers"))
    from simulatte.analysis.command import add_arguments as add_analysis_arguments

    add_analysis_arguments(commands.add_parser("analysis", help="Run reproducible numerical analyses"))
    worker = commands.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--stdio", action="store_true", required=True)
    commands._choices_actions = [action for action in commands._choices_actions if action.dest != "worker"]
    commands.metavar = "{studio,run,experiment,analysis,view,replay,store,workers}"
    args = parser.parse_args(argv)
    if args.command == "run":
        from simulatte.execution.command import run as execute

        return execute(args)
    if args.command in ("experiment", "replay", "store"):
        from simulatte.execution import command

        return {"experiment": command.experiment, "replay": command.replay, "store": command.store}[args.command](args)
    if args.command == "analysis":
        from simulatte.analysis.command import command

        return command(args)
    if args.command == "workers":
        from simulatte.execution.workers_cli import workers

        return workers(args)
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
