"""The local viewer capability boundary and byte-range contract."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from simulatte.cli import main
from simulatte.studio.server import ViewerServer


@pytest.fixture
def viewer(tmp_path):
    trace = tmp_path / "recording.simtrace"
    trace.write_bytes(b"0123456789")
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("viewer")
    (static / "manifest.json").write_text(json.dumps({"assets": {"index.html": "unused"}}))
    server = ViewerServer(trace, 8123, static)
    with TestClient(server.app, base_url=server.origin) as client:
        yield server, client
    server.close()


def authenticate(server, client):
    link = server.link()
    response = client.get(link)
    assert response.status_code == 200
    assert "location.replace" in response.text
    assert "sessionStorage.getItem" in response.text
    assert "nonce-" in response.headers["content-security-policy"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get(link).status_code == 403
    return {"X-Simulatte-Session": server.secret}


def test_server_capability_and_ranges(viewer):
    server, client = viewer
    assert client.get("/").text == "viewer"
    assert client.head("/").content == b""
    assert client.get("/trace").status_code == 403
    headers = authenticate(server, client)
    assert client.get("/trace").status_code == 403  # Cookie alone never grants access.
    for origin in ["http://attacker.invalid", "null", "http://127.0.0.1:8124"]:
        assert client.get("/trace", headers={**headers, "Origin": origin}).status_code == 403
    for host in ["localhost:8123", "attacker.invalid:8123", "127.0.0.1", "127.0.0.1:8124"]:
        assert client.get("/trace", headers={**headers, "Host": host}).status_code == 403
    full = client.get("/trace", headers={**headers, "Origin": server.origin})
    assert full.content == b"0123456789"
    assert "access-control-allow-origin" not in full.headers
    assert full.headers["cache-control"] == "no-store"
    head = client.head("/trace", headers=headers)
    assert head.headers["content-length"] == "10" and head.content == b""
    for value, expected, content_range in [
        ("bytes=2-4", b"234", "2-4"),
        ("bytes=7-", b"789", "7-9"),
        ("bytes=-2", b"89", "8-9"),
        ("bytes=8-99", b"89", "8-9"),
    ]:
        response = client.get("/trace", headers={**headers, "Range": value, "If-Match": head.headers["etag"]})
        assert response.status_code == 206 and response.content == expected
        assert response.headers["content-range"] == f"bytes {content_range}/10"
    assert client.get("/trace", headers={**headers, "If-Match": '"other"'}).status_code == 412
    for value in [
        "bytes=10-",
        "bytes=4-2",
        "bytes=-0",
        "bytes=1-2,4-5",
        "other=1-2",
        "bytes=a-b",
        "bytes=--",
        "bytes=-",
    ]:
        assert client.get("/trace", headers={**headers, "Range": value}).status_code == 416
    assert client.head("/trace", headers={**headers, "Range": "bytes=1-2"}).status_code == 206
    for path in ["/unknown", "/%2e%2e/secret", "/trace?path=other", "/manifest.json"]:
        assert client.get(path, headers=headers).status_code == 404
    assert client.post("/trace", headers=headers).status_code == 405


def test_links_renewal_head_and_instances(viewer):
    server, client = viewer
    old = server.link()
    new = server.link()
    assert client.get(old).status_code == 403
    assert client.head(new).status_code == 403
    assert client.get(new).status_code == 200
    client.cookies.clear()
    assert client.get("/trace", headers={"X-Simulatte-Session": server.secret}).status_code == 403


@pytest.mark.parametrize("change", ["write", "replace", "delete", "symlink"])
def test_changed_trace_rejected(viewer, change):
    server, client = viewer
    headers = authenticate(server, client)
    if change == "write":
        server.path.write_bytes(b"modified")
    else:
        server.path.unlink()
        if change == "replace":
            server.path.write_bytes(b"0123456789")
        elif change == "symlink":
            server.path.symlink_to(server.static / "index.html")
    assert client.get("/trace", headers=headers).status_code == 409


def test_reject_invalid_trace(viewer, tmp_path):
    server, _ = viewer
    link = tmp_path / "link"
    link.symlink_to(server.path)
    for path in [link, tmp_path / "missing", tmp_path]:
        with pytest.raises(ValueError, match="regular file"):
            ViewerServer(path, 8124, server.static)


def test_cli_help_and_errors(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    for port in ["-1", "65536"]:
        with pytest.raises(SystemExit):
            main(["view", "run.simtrace", "--port", port])
    with patch("simulatte.studio.server.serve") as serve:
        assert main(["view", "run.simtrace", "--no-browser"]) == 0
        serve.assert_called_once_with("run.simtrace", port=0, no_browser=True)
        serve.side_effect = ValueError("Readable file required")
        assert main(["view", "run.simtrace"]) == 2
    assert "Readable file required" in capsys.readouterr().err


def test_assets_missing_stale_and_valid(tmp_path, monkeypatch):
    import hashlib
    from simulatte.studio import server
    from importlib.metadata import version

    root = tmp_path
    directory = root / "src/simulatte/studio/static"
    directory.mkdir(parents=True)
    monkeypatch.setattr(server, "__file__", str(directory.parent / "server.py"))
    with pytest.raises(RuntimeError, match="Studio assets"):
        server.assets()
    asset = directory / "index.html"
    asset.write_bytes(b"viewer")
    manifest = {"version": version("simulatte"), "assets": {"index.html": hashlib.sha256(b"viewer").hexdigest()}}

    def publish():
        (directory / "manifest.json").write_text(json.dumps(manifest))

    publish()
    assert server.assets() == directory
    asset.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="Studio assets"):
        server.assets()
    asset.write_bytes(b"viewer")
    manifest["version"] = "other"
    publish()
    with pytest.raises(RuntimeError, match="Studio assets"):
        server.assets()
    manifest["version"] = version("simulatte")
    frontend = root / "studio"
    frontend.mkdir()
    (frontend / "source.ts").write_bytes(b"source")
    schema = root / "src/simulatte/layout/schema/layout-v1.schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_bytes(b"schema")
    digest = hashlib.sha256()
    digest.update(b"src/simulatte/layout/schema/layout-v1.schema.json\0" + hashlib.sha256(b"schema").digest())
    digest.update(b"studio/source.ts\0" + hashlib.sha256(b"source").digest())
    manifest["source_fingerprint"] = digest.hexdigest()
    publish()
    assert server.assets() == directory
    schema.write_bytes(b"changed schema")
    with pytest.raises(RuntimeError, match="Studio assets"):
        server.assets()
    schema.write_bytes(b"schema")
    (frontend / "source.ts").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="Studio assets"):
        server.assets()


@pytest.mark.parametrize("no_browser,opened,tty", [(True, False, False), (False, False, False), (False, True, True)])
def test_serve_lifetime(viewer, monkeypatch, capsys, no_browser, opened, tty):
    import io
    from simulatte.studio import server

    existing, _ = viewer
    monkeypatch.setattr(server, "assets", lambda: existing.static)
    monkeypatch.setattr(server.webbrowser, "open", lambda _: opened)

    class Input(io.StringIO):
        def isatty(self):
            return tty

    monkeypatch.setattr(server.sys, "stdin", Input("ignored\nlink\n"))

    class Thread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(server.threading, "Thread", Thread)
    calls = []
    monkeypatch.setattr(server.uvicorn, "Config", lambda app, **kw: (app, kw))

    class Runner:
        def __init__(self, config):
            self.config = config

        def run(self, *, sockets):
            assert sockets[0].getsockname()[0] == "127.0.0.1"
            calls.append(self.config)

    monkeypatch.setattr(server.uvicorn, "Server", Runner)
    server.serve(str(existing.path), no_browser=no_browser)
    assert calls[0][1]["access_log"] is False
    output = capsys.readouterr().out
    assert "Ctrl-C" in output
    assert ("/bootstrap?token=" in output) == (no_browser or tty)


def test_fifo_and_portable_read(viewer, tmp_path, monkeypatch):
    import os

    server, client = viewer
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match="regular file"):
            ViewerServer(fifo, 8124, server.static)
    monkeypatch.delattr(os, "pread", raising=False)
    headers = authenticate(server, client)
    assert client.get("/trace", headers={**headers, "Range": "bytes=2-4"}).content == b"234"


def test_portable_binary_ranges(viewer, tmp_path, monkeypatch):
    import os

    server, _ = viewer
    trace = tmp_path / "binary.simtrace"
    data = b"ab\r\n\x1a\xffcd"
    trace.write_bytes(data)
    monkeypatch.delattr(os, "pread", raising=False)
    binary = ViewerServer(trace, 8125, server.static)
    try:
        with TestClient(binary.app, base_url=binary.origin) as client:
            headers = authenticate(binary, client)
            assert client.get("/trace", headers=headers).content == data
            assert client.get("/trace", headers={**headers, "Range": "bytes=2-6"}).content == data[2:7]
    finally:
        binary.close()
