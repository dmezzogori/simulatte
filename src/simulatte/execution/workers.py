"""User worker configuration (``workers.toml``), SSH worker checks and the explicit uv setup step.

Credentials never live here: hosts, users, ports and keys come from ``~/.ssh/config``. The only
thing this module ever installs on a host is uv, through ``setup_worker`` and only after an
explicit yes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import tomllib

UV_INSTALL_COMMAND = "curl -LsSf https://astral.sh/uv/install.sh | sh"

CREDENTIALS_MESSAGE = "credentials are not accepted in workers.toml; use ~/.ssh/config"
DEFAULT_CACHE = "~/.cache/simulatte"
DEFAULT_PROVISION_TIMEOUT = 600.0

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_RESERVED = ("local", "interactive")
_FIELDS = frozenset({"host", "slots", "ssh_args", "uv", "cache", "provision_timeout"})
_CREDENTIAL_KEYS = frozenset(
    {"password", "passphrase", "token", "secret", "key", "private_key", "identity", "identity_file", "api_key"}
)
_CREDENTIAL_PARTS = ("password", "token", "secret")
_SSH_ENVIRONMENT = frozenset({"PATH", "HOME", "USER", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "TERM", "LANG"})
_STDERR_LIMIT = 4096
_PROBE_TIMEOUT = 30.0
_INSTALL_TIMEOUT = 300.0
_WINDOWS_PLATFORMS = ("windows", "mingw", "msys", "cygwin")


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    host: str
    slots: int
    ssh_args: tuple[str, ...]
    uv: str | None
    cache: str
    provision_timeout: float


@dataclass(frozen=True)
class WorkerCheck:
    reachable: bool
    platform: str | None
    shell: str | None
    uv_version: str | None
    cache_writable: bool | None
    messages: tuple[str, ...]


def default_workers_path() -> Path:
    override = os.environ.get("SIMULATTE_WORKERS")
    if override:
        return Path(override)
    return Path.home() / ".config" / "simulatte" / "workers.toml"


def load_workers(path: Path | str | None = None) -> dict[str, WorkerSpec]:
    """Read and strictly validate ``workers.toml``; a missing file means no remote workers."""
    path = default_workers_path() if path is None else Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ValueError(f"Cannot read workers file {path}: {error}") from error
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"Malformed workers file {path}: {error}") from error
    for key in document:
        if _is_credential(key):
            raise ValueError(f"{path}: {key}: {CREDENTIALS_MESSAGE}")
        if key != "workers":
            raise ValueError(f"{path}: unknown top-level key '{key}'")
    section = document.get("workers", {})
    if not isinstance(section, dict):
        raise ValueError(f"{path}: 'workers' must be a table of worker tables")
    return {name: _worker(path, name, entry) for name, entry in section.items()}


def resolve_workers(names: Iterable[str], workers: Mapping[str, WorkerSpec]) -> list[WorkerSpec]:
    resolved = []
    for name in names:
        if name not in workers:
            known = ", ".join(sorted(workers)) or "none"
            raise ValueError(f"Unknown worker '{name}'; known workers: {known}")
        resolved.append(workers[name])
    return resolved


def _is_credential(key: str) -> bool:
    lowered = key.lower()
    return lowered in _CREDENTIAL_KEYS or any(part in lowered for part in _CREDENTIAL_PARTS)


def _worker(path, name, entry) -> WorkerSpec:
    if not _NAME.fullmatch(name):
        raise ValueError(f"{path}: invalid worker name '{name}'; use letters, digits, '_', '.' or '-'")
    if name in _RESERVED or name.startswith("local-"):
        raise ValueError(f"{path}: worker name '{name}' is reserved")
    where = f"{path}: workers.{name}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: must be a table")
    for key in entry:
        if _is_credential(key):
            raise ValueError(f"{where}.{key}: {CREDENTIALS_MESSAGE}")
        if key not in _FIELDS:
            raise ValueError(f"{where}: unknown key '{key}'")
    if "host" not in entry:
        raise ValueError(f"{where}: 'host' is required")
    host = entry["host"]
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"{where}.host: must be a non-empty string")
    destination = host.rpartition("@")
    if destination[1] and ":" in destination[0]:
        raise ValueError(f"{where}.host: {CREDENTIALS_MESSAGE}")
    if host.startswith("-") or any(character.isspace() for character in host):
        raise ValueError(f"{where}.host: must not start with '-' or contain whitespace")
    slots = entry.get("slots", 1)
    if type(slots) is not int or slots < 1:
        raise ValueError(f"{where}.slots: must be an integer >= 1")
    ssh_args = entry.get("ssh_args", [])
    if not isinstance(ssh_args, list) or not all(isinstance(item, str) for item in ssh_args):
        raise ValueError(f"{where}.ssh_args: must be a list of strings")
    uv = entry.get("uv")
    cache = entry.get("cache", DEFAULT_CACHE)
    for field, value in (("uv", uv), ("cache", cache)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{where}.{field}: must be a non-empty string")
    timeout = entry.get("provision_timeout", DEFAULT_PROVISION_TIMEOUT)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{where}.provision_timeout: must be a number > 0")
    return WorkerSpec(name, host, slots, tuple(ssh_args), uv, cache, float(timeout))


# ----- SSH -----


def ssh_environment(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only environment ssh receives: no UV_*, SIMULATTE_* or any other parent variable."""
    parent = os.environ if parent is None else parent
    return {name: value for name, value in parent.items() if name in _SSH_ENVIRONMENT or name.startswith("LC_")}


def run_ssh(args: list[str], *, timeout: float) -> tuple[int, str, str]:
    done = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=ssh_environment(),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


SshRunner = Callable[..., tuple[int, str, str]]


def _bounded(text: str) -> str:
    return text.encode("utf-8", "replace")[:_STDERR_LIMIT].decode("utf-8", "ignore").strip()


def _remote_path(path: str) -> str:
    """Quote a host path for sh, keeping a leading ``~`` expandable."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def _argv(spec: WorkerSpec, command: str) -> list[str]:
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes"]
    return ["ssh", *options, *spec.ssh_args, spec.host, "--", "sh", "-c", shlex.quote(command)]


def _invoke(spec: WorkerSpec, command: str, ssh: SshRunner, timeout: float) -> tuple[int, str, str]:
    try:
        return ssh(_argv(spec, command), timeout=timeout)
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timed out"
    except OSError as error:
        return 255, "", f"Cannot run ssh: {error}"


def _probe(spec: WorkerSpec) -> str:
    candidates = ['"$(command -v uv 2>/dev/null)"', '"$HOME/.local/bin/uv"', '"$HOME/.cargo/bin/uv"']
    if spec.uv is not None:
        candidates.insert(0, _remote_path(spec.uv))
    return "\n".join(
        [
            "printf 'os=%s\\n' \"$(uname -s 2>/dev/null)\"",
            "printf 'arch=%s\\n' \"$(uname -m 2>/dev/null)\"",
            "printf 'shell=%s\\n' \"${SHELL:-sh}\"",
            "uvbin=",
            f"for candidate in {' '.join(candidates)}; do",
            '  if [ -n "$candidate" ] && [ -x "$candidate" ]; then uvbin=$candidate; break; fi',
            "done",
            "version=",
            'if [ -n "$uvbin" ]; then version=$("$uvbin" --version 2>/dev/null); fi',
            "printf 'uv=%s\\n' \"${version:-missing}\"",
            f"cache={_remote_path(spec.cache)}",
            'probe="$cache/.simulatte-probe.$$"',
            'if mkdir -p "$cache" 2>/dev/null && : > "$probe" 2>/dev/null; then',
            '  rm -f "$probe"; echo cache=writable',
            "else echo cache=unwritable; fi",
        ]
    )


def check_worker(spec: WorkerSpec, *, ssh: SshRunner = run_ssh) -> WorkerCheck:
    """One ssh invocation that reports platform, shell, uv and cache state; installs nothing."""
    code, stdout, stderr = _invoke(spec, _probe(spec), ssh, _PROBE_TIMEOUT)
    if code != 0:
        return WorkerCheck(False, None, None, None, None, (_bounded(stderr) or f"ssh exited with status {code}",))
    report = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in ("os", "arch", "shell", "uv", "cache"):
            report[key] = value.strip()
    system = report.get("os")
    if not system:
        return WorkerCheck(False, None, None, None, None, ("Unexpected output from the host probe",))
    platform = f"{system} {report['arch']}" if report.get("arch") else system
    shell = report.get("shell") or None
    if system.lower().startswith(_WINDOWS_PLATFORMS):
        return WorkerCheck(False, platform, shell, None, None, ("Windows hosts are not supported as workers",))
    version = report.get("uv", "missing")
    if version.startswith("uv "):
        version = version[3:].split()[0] if version[3:].split() else ""
    uv_version = None if version in ("", "missing") else version
    writable = report.get("cache") == "writable"
    messages = () if writable else (f"Cache directory {spec.cache} is not writable on the host",)
    return WorkerCheck(True, platform, shell, uv_version, writable, messages)


def format_check(spec: WorkerSpec, check: WorkerCheck) -> list[str]:
    lines = [f"worker {spec.name} ({spec.host})", f"  reachable: {'yes' if check.reachable else 'no'}"]
    if check.platform is not None:
        lines.append(f"  platform: {check.platform}")
    if check.shell is not None:
        lines.append(f"  shell: {check.shell}")
    if check.reachable:
        lines.append(f"  uv: {check.uv_version or 'missing'}")
        lines.append(f"  cache: {'writable' if check.cache_writable else 'not writable'}")
    lines.extend(f"  {message}" for message in check.messages)
    return lines


def setup_worker(
    spec: WorkerSpec,
    *,
    install_uv: bool = False,
    confirm: Callable[[str], str] = input,
    ssh: SshRunner = run_ssh,
    say: Callable[[str], object] = print,
) -> WorkerCheck:
    """Check a host and, on explicit request and confirmation, install uv. Nothing else is installed."""
    check = check_worker(spec, ssh=ssh)
    for line in format_check(spec, check):
        say(line)
    if not check.reachable or check.uv_version is not None:
        return check
    if not install_uv:
        say("uv is missing; rerun with --install-uv to install it with the official installer")
        return check
    say(f"Would run on {spec.host}: {UV_INSTALL_COMMAND}")
    try:
        answer = confirm(f"Install uv on {spec.host} with the official installer? [y/N] ")
    except EOFError:
        answer = "n"
    if answer.strip().lower() not in ("y", "yes"):
        say("uv not installed")
        return check
    code, _, stderr = _invoke(spec, UV_INSTALL_COMMAND, ssh, _INSTALL_TIMEOUT)
    after = check_worker(spec, ssh=ssh)
    if code != 0:
        detail = _bounded(stderr)
        failure = f"uv installer failed (exit {code})" + (f": {detail}" if detail else "")
        after = replace(after, messages=(failure, *after.messages))
    for line in format_check(spec, after):
        say(line)
    return after
