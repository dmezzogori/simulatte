"""Studio CLI shares data-only parsing and keeps web imports optional."""

import argparse
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from simulatte.execution.command import add_arguments
from simulatte.studio.command import configure, server_config


def arguments(*values):
    parser = argparse.ArgumentParser()
    add_arguments(parser, headless=False)
    return parser.parse_args(values)


def test_studio_cli_keeps_missing_fields_for_forms():
    from simulatte.model import ParameterField, ParameterSchema

    fields = (ParameterField("n", "int", True), ParameterField("label", "str", True))
    prepared = SimpleNamespace(
        metadata={
            "selected": "build",
            "models": [
                {
                    "name": "build",
                    "inputs": [],
                    "parameters": ParameterSchema(fields).to_dict(),
                    "stopping": {"type": "horizon", "horizon": 10, "warmup": 0},
                }
            ],
        }
    )
    options = configure(arguments("plant.py", "--param", "n=4", "--seed", "18"))(prepared)
    assert options["parameters"] == {"n": 4} and options["seed"] == 18
    for raw in ("wrong=2", "n=bad", "n"):
        with pytest.raises(ValueError):
            configure(arguments("plant.py", "--param", raw))(prepared)
    with pytest.raises(ValueError):
        configure(arguments("plant.py", "--param", "n=2", "--param", "n=3"))(prepared)


def test_uvicorn_configuration_uses_bounded_backend_explicitly():
    from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol

    config = server_config(object())
    assert config.ws is WebSocketProtocol and config.loop == "asyncio"
    assert config.ws_max_size == 256 << 10 and config.ws_max_queue == 16
    assert not config.ws_per_message_deflate and not config.proxy_headers
    assert not config.access_log and not config.reload and config.workers == 1


@pytest.mark.parametrize("browser,opened", [(False, False), (True, False), (True, True)])
def test_studio_serving_lifecycle_and_terminal_link(tmp_path, monkeypatch, capsys, browser, opened):
    import io
    import sys
    from simulatte.studio import command
    from tests.execution.test_coordinator import project

    spec = project(tmp_path)
    static = tmp_path / "static"
    static.mkdir()
    (static / "manifest.json").write_text('{"assets": {}}')
    monkeypatch.setattr(command, "assets", lambda: static)
    args = arguments(str(spec.root / "plant.py") + ":build", "--project", str(spec.root))
    args.port, args.no_browser = 0, not browser
    stdin = io.StringIO("ignored\nlink\n")
    monkeypatch.setattr(stdin, "isatty", lambda: browser)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(command.webbrowser, "open", lambda link: opened)
    seen = []

    class Server:
        def __init__(self, config):
            seen.append(config.app)

        def run(self, *, sockets):
            assert sockets[0].getsockname()[0] == "127.0.0.1"

    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(command.uvicorn, "Server", Server)
    monkeypatch.setattr(command, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "missing-workers.toml"))
    command.serve_studio(args)
    assert len(seen) == 1
    assert "Simulatte Studio:" in capsys.readouterr().out
    # The CLI context releases ownership even if ASGI lifespan never starts.
    from simulatte.execution.store import RunStore

    with RunStore.open(spec.root / ".simulatte"):
        pass


def test_studio_cli_dispatch_and_startup_errors(monkeypatch, capsys):
    from simulatte.cli import main
    from simulatte.studio import command

    calls = []
    monkeypatch.setattr(command, "serve_studio", lambda args: calls.append(args))
    assert main(["studio", "plant.py", "--no-browser"]) == 0
    assert calls[0].no_browser

    def fail(args):
        raise ValueError("invalid project")

    monkeypatch.setattr(command, "serve_studio", fail)
    assert main(["studio", "plant.py"]) == 2
    assert "invalid project" in capsys.readouterr().err


@pytest.mark.parametrize("text", ['[workers.box]\nhost = "box"\nslots = 2\n', "[workers.box\nhost = "])
def test_studio_slots_use_configured_workers_and_report_a_malformed_file(tmp_path, monkeypatch, text):
    from simulatte.execution.slots import default_local_slots
    from simulatte.studio import command
    from tests.execution.test_coordinator import project

    spec = project(tmp_path)
    (tmp_path / "workers.toml").write_text(text)
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "workers.toml"))
    monkeypatch.setattr(command, "assets", lambda: tmp_path)
    seen = []

    class Stop(Exception):
        pass

    def studio(coordinator, port, static, **options):
        seen.append((coordinator.slots, options["workers_error"]))
        raise Stop

    monkeypatch.setattr(command, "StudioServer", studio)
    args = arguments(str(spec.root / "plant.py"), "--project", str(spec.root))
    args.port = 0
    with pytest.raises(Stop):
        command.serve_studio(args)
    ((slots, error),) = seen
    assert slots.local == default_local_slots()
    if text.endswith("slots = 2\n"):
        assert [(spec.name, spec.slots) for spec in slots.workers] == [("box", 2)] and error is None
    else:
        assert slots.workers == () and error.startswith("Malformed workers file")
