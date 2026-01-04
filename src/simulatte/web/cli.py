#!/usr/bin/env python
"""CLI entry point for Simulatte Web UI."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import NoReturn


def main() -> NoReturn:
    """Main entry point for the simulatte CLI."""
    parser = argparse.ArgumentParser(
        description="Start Simulatte Web UI for simulation visualization and analytics",
        prog="simulatte",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
  simulatte my_simulation.py
  simulatte my_simulation.py --port 8080
  simulatte my_simulation.py --no-browser

Your simulation module should define a 'Simulation' class:

  class Simulation:
      def setup(self, env):
          # Configure shopfloor, servers, etc.
          return SimulationComponents(shopfloor=..., servers=...)

      def run(self, until):
          self.env.run(until=until)
""",
    )

    parser.add_argument(
        "module",
        type=Path,
        help="Path to Python file containing Simulation class",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to run server on (default: 8765)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database (default: .simulatte/db.sqlite)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    args = parser.parse_args()

    # Validate module path
    if not args.module.exists():
        print(f"Error: File not found: {args.module}", file=sys.stderr)
        sys.exit(1)

    if not args.module.is_file():
        print(f"Error: Not a file: {args.module}", file=sys.stderr)
        sys.exit(1)

    if not args.module.suffix == ".py":
        print(f"Error: Expected a Python file (.py), got: {args.module}", file=sys.stderr)
        sys.exit(1)

    # Import here to avoid slow startup if just showing help
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: uvicorn not installed. Install web dependencies:\n  uv pip install simulatte[web]",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load simulation class to validate it early
    from simulatte.web.simulation_bridge import load_simulation_class

    try:
        simulation_class = load_simulation_class(args.module.absolute())
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error loading simulation: {e}", file=sys.stderr)
        sys.exit(1)
    except TypeError as e:
        print(f"Error: Invalid Simulation class: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading module: {e}", file=sys.stderr)
        sys.exit(1)

    # Database path
    db_path = args.db or Path(".simulatte/db.sqlite")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Create app with simulation class
    from simulatte.web.app import create_app, set_simulation_class

    set_simulation_class(simulation_class)
    app = create_app(db_path)

    url = f"http://{args.host}:{args.port}"

    # Print startup banner
    print()
    print("  \033[1mSimulatte Web UI\033[0m")
    print("  " + "─" * 40)
    print(f"  Local:    \033[36m{url}\033[0m")
    print(f"  Module:   {args.module.absolute()}")
    print(f"  Database: {db_path.absolute()}")
    print()
    print("  Press \033[1mCtrl+C\033[0m to stop")
    print()

    # Open browser
    if not args.no_browser:
        webbrowser.open(url)

    # Run server
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="warning",
            reload=args.reload,
        )
    except KeyboardInterrupt:
        print("\n  Shutting down...")

    sys.exit(0)


if __name__ == "__main__":
    main()
