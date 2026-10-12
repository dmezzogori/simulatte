"""Native API ordering/failure unit checks; native Windows integration is separate."""

from __future__ import annotations

import ctypes as c
import os
import sys

import pytest

from simulatte.execution import windows_job as m


class Function:
    def __init__(self, api, name):
        self.api, self.name = api, name

    def __call__(self, *args):
        return self.api.call(self.name, args)


class Kernel:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.running = True

    def __getattr__(self, name):
        function = Function(self, name)
        setattr(self, name, function)
        return function

    def call(self, name, args):
        self.calls.append(name)
        if name == "InitializeProcThreadAttributeList" and args[0] is None:
            args[3]._obj.value = 64
            return 0
        if name == self.failure:
            if name == "CreateFileW":
                return m.HANDLE(-1).value
            return 0xFFFFFFFF if name == "ResumeThread" else 0
        if name == "CreateJobObjectW":
            return 10
        if name == "CreateFileW":
            return 11
        if name == "GetStdHandle":
            return 12 if args[0] == m.DWORD(-11).value else 13
        if name == "SetInformationJobObject":
            assert args[2]._obj.basic.flags == 0x2000
        if name == "UpdateProcThreadAttribute":
            assert args[2] == 0x20002
            assert list(args[3]) == [11, 12, 13]
        if name == "CreateProcessW":
            assert args[5] & 0x4  # User code cannot run until Job assignment.
            assert not args[5] & 0x01000000  # CREATE_BREAKAWAY_FROM_JOB forbidden.
            args[9]._obj.process = 20
            args[9]._obj.thread = 21
            args[9]._obj.pid = 22
        if name == "ResumeThread":
            assert self.calls.index("AssignProcessToJobObject") < self.calls.index("ResumeThread")
            return 1
        if name in ("TerminateProcess", "TerminateJobObject"):
            self.running = False
        if name == "WaitForSingleObject":
            return 258 if self.running else 0
        if name == "GetExitCodeProcess":
            args[1]._obj.value = 1
        return 1


def install(monkeypatch, failure=None):
    fake = Kernel(failure)
    monkeypatch.setattr(c, "WinDLL", lambda *a, **k: fake, raising=False)
    monkeypatch.setattr(c, "get_last_error", lambda: 5, raising=False)
    return fake


def test_windows_suspended_assignment_and_repeated_cleanup(monkeypatch):
    fake = install(monkeypatch)
    process = m.JobProcess(["python.exe", "-c", "pass"])
    assert process.pid == 22
    assert process.poll() is None
    process.terminate()
    assert process.wait() == 1
    assert process.poll() == 1
    process.close()
    process.close()
    assert fake.calls.count("ResumeThread") == 1


@pytest.mark.parametrize(
    "failure",
    [
        "CreateJobObjectW",
        "SetInformationJobObject",
        "CreateFileW",
        "SetHandleInformation",
        "InitializeProcThreadAttributeList",
        "UpdateProcThreadAttribute",
        "CreateProcessW",
        "AssignProcessToJobObject",
        "ResumeThread",
    ],
)
def test_assignment_and_setup_failure_never_execute_user_code(monkeypatch, failure):
    fake = install(monkeypatch, failure)
    with pytest.raises(OSError):
        m.JobProcess(["python.exe", "-c", "pass"])
    if failure != "ResumeThread":
        assert "ResumeThread" not in fake.calls
    if failure in ("AssignProcessToJobObject", "ResumeThread"):
        assert "TerminateProcess" in fake.calls


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows Job API")
def test_native_windows_containment_smoke():
    process = m.JobProcess([sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"])
    try:
        assert process.poll() is None
        process.kill()
        assert process.wait() == 1
    finally:
        process.close()


def test_windows_environment_is_explicit_and_rejects_invalid_entries(monkeypatch):
    install(monkeypatch)
    process = m.JobProcess(["python.exe"], env={"SIMULATTE_CHILD_TOKEN": "secret", "PATH": "C:\\tools"})
    process.terminate()
    process.wait()
    process.close()
    with pytest.raises(ValueError, match="environment"):
        m.JobProcess(["python.exe"], env={"BAD=KEY": "value"})


def test_guardian_windows_backend_loads_without_importing_model_code(monkeypatch):
    from types import SimpleNamespace
    from simulatte.execution import process as containment

    install(monkeypatch)
    monkeypatch.setattr(containment, "os", SimpleNamespace(name="nt", environ={}))
    process = containment._launch(["python.exe"], None, {"TOKEN": "value"})
    process.terminate()
    assert process.wait() == 1
    process.close()


def test_windows_poll_api_failures_are_not_success(monkeypatch):
    fake = install(monkeypatch)
    process = m.JobProcess(["python.exe"])
    try:
        fake.WaitForSingleObject = lambda *args: 0xFFFFFFFF
        with pytest.raises(OSError, match="wait"):
            process.poll()
        fake.WaitForSingleObject = lambda *args: 0
        fake.failure = "GetExitCodeProcess"
        with pytest.raises(OSError, match="exit code"):
            process.poll()
    finally:
        process.close()


def test_windows_attribute_allocation_and_wait_failure_refuse_progress(monkeypatch):
    fake = install(monkeypatch)
    fake.InitializeProcThreadAttributeList = lambda *args: 0
    with pytest.raises(OSError, match="allocation"):
        m.JobProcess(["python.exe"])
    assert "CreateProcessW" not in fake.calls
    fake = install(monkeypatch)
    process = m.JobProcess(["python.exe"])
    try:
        fake.WaitForSingleObject = lambda *args: 0xFFFFFFFF
        with pytest.raises(OSError, match="wait"):
            process.wait()
    finally:
        process.close()
