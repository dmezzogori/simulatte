"""Loopback Studio startup, with model inspection confined to execution children."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn
from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol

from simulatte.execution.command import _stopping
from simulatte.execution.coordinator import Coordinator, selected_model
from simulatte.execution.slots import SlotConfig
from simulatte.execution.snapshot import discover_project
from simulatte.execution.workers import load_workers
from simulatte.model import ParameterSchema, parse_parameters
from simulatte.studio.app import StudioServer
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
    static = assets()
    path, separator, name = args.model.rpartition(":")
    selected = bool(separator and path.endswith(".py"))
    spec = discover_project(path if selected else args.model, args.project, layout=args.layout)
    reference = spec.model + (":" + name if selected else "")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", args.port))
        sock.listen(128)
        # Default local experiment slots plus the configured workers; a malformed workers.toml
        # is reported in state.get rather than stopping Studio. Remote slots connect only when used.
        try:
            workers, workers_error = tuple(load_workers().values()), None
        except ValueError as error:
            workers, workers_error = (), str(error)[:4096]
        with Coordinator(spec, reference=reference, slots=SlotConfig(workers=workers)) as coordinator:
            studio = StudioServer(
                coordinator,
                sock.getsockname()[1],
                static,
                timeout=args.timeout,
                configure=configure(args),
                workers_error=workers_error,
            )
            print(f"Simulatte Studio: {studio.origin}\nCtrl-C to stop. Enter link for a fresh one-time browser link.")
            link = studio.link()
            if args.no_browser:
                print(link, flush=True)
            elif not webbrowser.open(link):
                print("Browser could not open. Enter link below, or restart with --no-browser.", flush=True)

            def terminal():
                for line in sys.stdin:
                    if line.strip() == "link":
                        print(studio.link(), flush=True)

            if sys.stdin.isatty():
                threading.Thread(target=terminal, daemon=True).start()
            uvicorn.Server(server_config(studio.app)).run(sockets=[sock])
