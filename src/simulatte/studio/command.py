"""Loopback Studio startup, with model inspection confined to execution children."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn
from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol

from simulatte.execution.command import _stopping
from simulatte.execution.coordinator import selected_model
from simulatte.model import ParameterSchema, parse_parameters
from simulatte.studio.server import assets


def configure(args):
    def initial(prepared):
        model = selected_model(prepared.metadata)
        raw = []
        for item in args.param:
            name, separator, value = item.partition("=")
            if not separator:
                raise ValueError("Parameters use NAME=VALUE")
            raw.append((name, value))
        schema = ParameterSchema.from_dict(model["parameters"])
        # Parse only provided fields; absent required values remain form issues.
        names = {name for name, _ in raw}
        partial = ParameterSchema(tuple(field for field in schema.fields if field.name in names))
        return {
            "parameters": parse_parameters(partial, raw),
            "seed": args.seed,
            "stopping": _stopping(args, model["stopping"]),
            "level": args.level,
            "kpis": args.kpi,
        }

    setattr(initial, "flags", vars(args))
    return initial


def server_config(app):
    # The alias "websockets" will change meaning in Uvicorn. This backend's
    # bounded receive queue is part of our transport contract.
    return uvicorn.Config(
        app,
        loop="asyncio",
        http="h11",
        ws=WebSocketProtocol,
        ws_max_size=256 << 10,
        ws_max_queue=16,
        ws_per_message_deflate=False,
        proxy_headers=False,
        access_log=False,
        log_level="critical",
        reload=False,
        workers=1,
    )


def serve_studio(args):
    from pathlib import Path
    from simulatte.projects import resolve_target, StudioTarget
    from simulatte.projects.bootstrap import launch_project

    target = resolve_target(args.model, cwd=Path.cwd())
    if args.project is not None:
        root = Path(args.project).resolve()
        if not target.root.is_relative_to(root):
            raise ValueError("Model target escapes explicit project root")
        target = StudioTarget(root, target.legacy_reference)
    launch_project(target, port=args.port, no_browser=args.no_browser, configure=configure(args))


def serve_project(service, *, port, no_browser):
    from simulatte.studio.project_commands import ProjectServer

    static = assets()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        studio = ProjectServer(service, sock.getsockname()[1], static)
        server = uvicorn.Server(server_config(studio.app))
        studio.on_restart = lambda: setattr(server, "should_exit", True)
        print(f"Simulatte Studio: {studio.origin}\nCtrl-C to stop. Enter link for a fresh one-time browser link.")
        link = studio.link()
        if no_browser or not webbrowser.open(link):
            print(link, flush=True)

        def terminal():
            for line in sys.stdin:
                if line.strip() == "link":
                    print(studio.link(), flush=True)

        if sys.stdin.isatty():
            threading.Thread(target=terminal, daemon=True).start()
        server.run(sockets=[sock])
