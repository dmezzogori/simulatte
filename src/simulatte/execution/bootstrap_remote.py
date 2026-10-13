"""POSIX ``sh`` text run on remote hosts: probe, stage and start the frozen trusted runtime.

Every script is built from validated values, quoted with ``shlex.quote`` (``~`` paths expand to
``$HOME``) and travels as the remote command text alone; no environment variable is forwarded.
"""

from __future__ import annotations

import re
import shlex

from simulatte.execution.workers import _remote_path

MAX_SCRIPT = 8 << 10
UV_MISSING = 3
RUNTIME_MISSING = 4
HASH_MISMATCH = 3

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PYTHON = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+-]*")
_NAME = re.compile(r"[A-Za-z0-9_.-]+")

# The supervisor's only roots: the staged package and its supervisor-only dependency copies.
_SUPERVISOR = (
    "import os, sys; root = sys.argv[1]; sys.path[:0] = [root, root + '/supervisor-deps']; "
    "from simulatte.cli import main; os._exit(main(['_worker', *sys.argv[2:5]]))"
)


def _sha(value: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError("Expected a lowercase SHA-256 hex digest")
    return value


def _path(value: str) -> str:
    if type(value) is not str or not value.strip() or "\0" in value:
        raise ValueError("Remote path must be a non-empty string")
    return _remote_path(value)


def _bounded(text: str) -> str:
    if len(text.encode("utf-8")) > MAX_SCRIPT:
        raise ValueError("Remote script exceeds 8 KiB")
    return text


def probe_script(cache: str, sha256: str) -> str:
    """Exit 0 when ``<cache>/runtime/<sha256>`` is staged, non-zero otherwise."""
    return _bounded(f"test -f {_path(cache)}/runtime/{_sha(sha256)}/ready")


def stage_script(cache: str, sha256: str, length: int) -> str:
    """Read exactly ``length`` archive bytes from stdin, verify, extract and publish atomically."""
    if type(length) is not int or length < 0:
        raise ValueError("Archive length must be a non-negative integer")
    sha = shlex.quote(_sha(sha256))
    lines = [
        f"cache={_path(cache)}",
        f"sha={sha}",
        'runtime="$cache/runtime"',
        'part="$runtime/$sha.tar.part.$$"',
        "tmp=",
        'trap \'rm -f "$part"; if [ -n "$tmp" ]; then rm -rf "$tmp"; fi\' EXIT',
        'mkdir -p "$runtime" || exit 1',
        f'head -c {length} > "$part" || exit 1',
        f'if [ "$(wc -c < "$part" | tr -d " ")" != {length} ]; then',
        '  echo "simulatte: runtime archive was truncated" >&2; exit 5',
        "fi",
        "if command -v sha256sum >/dev/null 2>&1; then",
        '  actual=$(sha256sum "$part" | cut -d " " -f 1)',
        "elif command -v shasum >/dev/null 2>&1; then",
        '  actual=$(shasum -a 256 "$part" | cut -d " " -f 1)',
        "else",
        '  echo "simulatte: neither sha256sum nor shasum is available on the host" >&2; exit 5',
        "fi",
        'if [ "$actual" != "$sha" ]; then',
        f'  echo "simulatte: runtime archive hash mismatch" >&2; exit {HASH_MISMATCH}',
        "fi",
        'tmp=$(mktemp -d "$runtime/.stage.XXXXXX") || exit 1',
        'tar -xf "$part" -C "$tmp" || exit 1',
        ': > "$tmp/ready" || exit 1',
        "name=${tmp##*/}",
        'if [ -d "$runtime/$sha" ]; then rm -rf "$tmp"; else mv "$tmp" "$runtime/$sha" || exit 1; fi',
        "tmp=",
        # A concurrent stage that published between the check and mv makes mv nest this copy.
        'rm -rf "$runtime/$sha/$name"',
        'test -f "$runtime/$sha/ready" || exit 1',
        'echo "staged $sha"',
    ]
    return _bounded("\n".join(lines))


def bootstrap_script(cache: str, sha256: str, python: str, uv: str | None, worker: str, runtime_hash: str) -> str:
    """Locate uv, require the staged runtime and ``exec`` the supervisor under ``uv run --python``.

    The supervisor receives the runtime hash, the cache root (staging, environments) and uv's path.
    """
    if type(python) is not str or not _PYTHON.fullmatch(python):
        raise ValueError("Invalid Python version request")
    if type(worker) is not str or not _NAME.fullmatch(worker):
        raise ValueError("Invalid worker name")
    candidates = ['"$(command -v uv 2>/dev/null)"', '"$HOME/.local/bin/uv"', '"$HOME/.cargo/bin/uv"']
    if uv is not None:
        candidates.insert(0, _path(uv))
    missing = f"simulatte: uv is missing; run: simulatte workers setup {worker} --install-uv"
    command = ["run", "--no-project", "--python", python, "--no-sync", "--", "python", "-I", "-S", "-c", _SUPERVISOR]
    lines = [
        f"cache={_path(cache)}",
        f'dir="$cache/runtime/"{shlex.quote(_sha(sha256))}',
        "uvbin=",
        f"for candidate in {' '.join(candidates)}; do",
        '  if [ -n "$candidate" ] && [ -x "$candidate" ]; then uvbin=$candidate; break; fi',
        "done",
        f'if [ -z "$uvbin" ]; then echo {shlex.quote(missing)} >&2; exit {UV_MISSING}; fi',
        'if [ ! -f "$dir/ready" ]; then',
        f"  echo {shlex.quote('simulatte: trusted runtime ' + sha256 + ' is not staged')} >&2; exit {RUNTIME_MISSING}",
        "fi",
        f'exec "$uvbin" {shlex.join(command)} "$dir" {shlex.quote(_sha(runtime_hash))} "$cache" "$uvbin"',
    ]
    return _bounded("\n".join(lines))
