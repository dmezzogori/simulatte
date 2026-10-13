"""SSH supervisor sessions on remote workers, bootstrapped from the staged trusted runtime.

``SshTransport.start`` probes the host for the frozen runtime archive, stages it through a
separate ``ssh`` call when absent, then runs the bootstrap as the remote command. The ssh client
gets the allowlisted environment only; the remote side receives nothing but the command text.
"""

from __future__ import annotations

import subprocess

from simulatte.execution import bootstrap_remote as scripts
from simulatte.execution.snapshot import RuntimeSnapshot
from simulatte.execution.transport import FramedChannel, _PipeTransport
from simulatte.execution.workers import WorkerSpec, _argv, ssh_environment

PROBE_TIMEOUT = 30.0
STAGE_TIMEOUT = 120.0
_TAIL = 4096


class BootstrapError(OSError):
    """A remote bootstrap step failed for a known reason (``code``) rather than by transport loss.

    Codes: ``uv_missing``, ``runtime_missing``, ``runtime_hash_mismatch``, ``staging_failed``,
    ``probe_failed``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _tail(data: bytes) -> str:
    return data[-_TAIL:].decode("utf-8", errors="replace").strip()


class SshTransport(_PipeTransport):
    """One ssh client per slot; the remote supervisor's stdin EOF ends its children."""

    @classmethod
    def start(cls, spec: WorkerSpec, *, runtime: RuntimeSnapshot, python: str, ssh_binary: str = "ssh") -> SshTransport:
        def argv(command: str) -> list[str]:
            return [ssh_binary, *_argv(spec, command)[1:]]

        sha = runtime.archive_sha256
        try:
            probe = subprocess.run(
                argv(scripts.probe_script(spec.cache, sha)),
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=ssh_environment(),
                timeout=PROBE_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise BootstrapError("probe_failed", f"ssh probe of {spec.name} timed out") from error
        if probe.returncode not in (0, 1):
            raise BootstrapError(
                "probe_failed", f"ssh probe of {spec.name} failed (exit {probe.returncode}): {_tail(probe.stderr)}"
            )
        if probe.returncode == 1:
            cls._stage(spec, runtime, argv)
        command = scripts.bootstrap_script(spec.cache, sha, python, spec.uv, spec.name, runtime.content_hash)
        process = subprocess.Popen(
            argv(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=ssh_environment(),
        )
        # The channel writes MAGIC first; banners and MOTD before the supervisor's magic are
        # bounded pre-handshake noise (64 KiB, 10 s) in the frame codec.
        channel = FramedChannel(process.stdout, process.stdin)
        channel.send("hello", {"protocol": [1, 0]})
        return cls(process, channel)

    @staticmethod
    def _stage(spec: WorkerSpec, runtime: RuntimeSnapshot, argv) -> None:
        sha = runtime.archive_sha256
        with runtime.archive.open("rb") as stream:
            length = runtime.archive.stat().st_size
            try:
                done = subprocess.run(
                    argv(scripts.stage_script(spec.cache, sha, length)),
                    stdin=stream,
                    capture_output=True,
                    env=ssh_environment(),
                    timeout=STAGE_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise BootstrapError("staging_failed", f"Staging the runtime on {spec.name} timed out") from error
        detail = _tail(done.stderr)
        if done.returncode == scripts.HASH_MISMATCH:
            raise BootstrapError("runtime_hash_mismatch", f"Runtime archive hash mismatch on {spec.name}: {detail}")
        # Shell startup files may print before the script; its own last line is the verdict.
        if done.returncode != 0 or done.stdout.split()[-2:] != [b"staged", sha.encode()]:
            raise BootstrapError(
                "staging_failed", f"Staging the runtime on {spec.name} failed (exit {done.returncode}): {detail}"
            )

    def _closed_error(self) -> OSError:
        # Name the channel failure (EOF, banner noise over 64 KiB, framing) before the stderr tail.
        error = OSError(f"{self.channel.error}; {super()._closed_error()}")
        try:
            code = self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return error
        reason = {scripts.UV_MISSING: "uv_missing", scripts.RUNTIME_MISSING: "runtime_missing"}.get(code)
        return error if reason is None else BootstrapError(reason, str(error))

    def _stop(self):
        """Terminate, then kill, the ssh client; the remote side sees EOF and ends its children."""
        self.process.terminate()
        try:
            self.process.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            pass
        super()._stop()
