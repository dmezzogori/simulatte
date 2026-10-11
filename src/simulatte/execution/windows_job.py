"""Windows suspended creation and kill-on-close Job containment.

No breakaway flags are enabled. Assignment failure terminates the still-suspended
child before any user instruction can run. Kept independent of Simulatte imports
so the isolated guardian can load this sibling module directly.
"""

from __future__ import annotations

import ctypes as c
import subprocess

DWORD = c.c_uint32
WORD = c.c_uint16
HANDLE = c.c_void_p
SIZE = c.c_size_t


class BasicLimits(c.Structure):
    _fields_ = [
        ("per_process_time", c.c_int64),
        ("per_job_time", c.c_int64),
        ("flags", DWORD),
        ("min_working_set", SIZE),
        ("max_working_set", SIZE),
        ("active_processes", DWORD),
        ("affinity", SIZE),
        ("priority", DWORD),
        ("scheduling", DWORD),
    ]


class JobLimits(c.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io_counters", c.c_uint64 * 6),
        ("process_memory", SIZE),
        ("job_memory", SIZE),
        ("peak_process_memory", SIZE),
        ("peak_job_memory", SIZE),
    ]


class StartupInfo(c.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("reserved", c.c_wchar_p),
        ("desktop", c.c_wchar_p),
        ("title", c.c_wchar_p),
        ("x", DWORD),
        ("y", DWORD),
        ("xsize", DWORD),
        ("ysize", DWORD),
        ("xchars", DWORD),
        ("ychars", DWORD),
        ("fill", DWORD),
        ("flags", DWORD),
        ("show", WORD),
        ("reserved_size", WORD),
        ("reserved_bytes", HANDLE),
        ("stdin", HANDLE),
        ("stdout", HANDLE),
        ("stderr", HANDLE),
    ]


class StartupInfoEx(c.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", HANDLE)]


class ProcessInfo(c.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


def kernel():
    # Loaded only on Windows. Explicit prototypes prevent 64-bit handle truncation.
    api = getattr(c, "WinDLL")("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": (HANDLE, [HANDLE, c.c_wchar_p]),
        "SetInformationJobObject": (c.c_int32, [HANDLE, c.c_int32, HANDLE, DWORD]),
        "CreateFileW": (HANDLE, [c.c_wchar_p, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE]),
        "GetStdHandle": (HANDLE, [DWORD]),
        "SetHandleInformation": (c.c_int32, [HANDLE, DWORD, DWORD]),
        "InitializeProcThreadAttributeList": (c.c_int32, [HANDLE, DWORD, DWORD, c.POINTER(SIZE)]),
        "UpdateProcThreadAttribute": (c.c_int32, [HANDLE, DWORD, SIZE, HANDLE, SIZE, HANDLE, HANDLE]),
        "DeleteProcThreadAttributeList": (None, [HANDLE]),
        "CreateProcessW": (
            c.c_int32,
            [
                c.c_wchar_p,
                c.c_wchar_p,
                HANDLE,
                HANDLE,
                c.c_int32,
                DWORD,
                HANDLE,
                c.c_wchar_p,
                c.POINTER(StartupInfoEx),
                c.POINTER(ProcessInfo),
            ],
        ),
        "AssignProcessToJobObject": (c.c_int32, [HANDLE, HANDLE]),
        "ResumeThread": (DWORD, [HANDLE]),
        "TerminateProcess": (c.c_int32, [HANDLE, DWORD]),
        "TerminateJobObject": (c.c_int32, [HANDLE, DWORD]),
        "WaitForSingleObject": (DWORD, [HANDLE, DWORD]),
        "GetExitCodeProcess": (c.c_int32, [HANDLE, c.POINTER(DWORD)]),
        "CloseHandle": (c.c_int32, [HANDLE]),
    }
    for name, (restype, args) in signatures.items():
        fn = getattr(api, name)
        fn.restype, fn.argtypes = restype, args
    return api


def checked(value, operation):
    if not value:
        code = getattr(c, "get_last_error")()
        raise OSError(code, f"Windows containment: {operation} failed")
    return value


class JobProcess:
    def __init__(self, argv: list[str], cwd: str | None = None, *, env: dict[str, str] | None = None):
        self.api = api = kernel()
        self.job = None
        self.process = None
        self.returncode = None
        self.pid = 0
        thread = null = attributes = None
        initialized = False
        try:
            self.job = checked(api.CreateJobObjectW(None, None), "create Job")
            limits = JobLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(api.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)), "set Job limits")
            null = api.CreateFileW("NUL", 0x80000000, 3, None, 3, 0x80, None)
            if null in (None, HANDLE(-1).value):
                raise OSError("Windows containment: open NUL failed")
            handles = (null, api.GetStdHandle(DWORD(-11).value), api.GetStdHandle(DWORD(-12).value))
            for handle in handles:
                checked(api.SetHandleInformation(handle, 1, 1), "set inheritable handle")
            size = SIZE()
            api.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
            if not size.value:
                raise OSError("Windows containment: attribute allocation failed")
            attributes = c.create_string_buffer(size.value)
            checked(api.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)), "initialize handles")
            initialized = True
            inherited = (HANDLE * len(set(handles)))(*dict.fromkeys(handles))
            checked(
                api.UpdateProcThreadAttribute(attributes, 0, 0x20002, inherited, c.sizeof(inherited), None, None),
                "restrict inherited handles",
            )
            startup = StartupInfoEx()
            startup.startup.cb = c.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = c.cast(attributes, HANDLE)
            info = ProcessInfo()
            command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = None
            if env is not None:
                if any("\0" in key or "=" in key or "\0" in value for key, value in env.items()):
                    raise ValueError("Invalid process environment")
                environment = c.create_unicode_buffer(
                    "\0".join(f"{key}={env[key]}" for key in sorted(env, key=str.casefold)) + "\0"
                )
            checked(
                api.CreateProcessW(
                    argv[0],
                    command,
                    None,
                    None,
                    True,
                    0x4 | 0x80000 | 0x8000000 | (0x400 if environment is not None else 0),
                    environment,
                    cwd,
                    c.byref(startup),
                    c.byref(info),
                ),
                "create suspended process",
            )
            self.process, thread, self.pid = info.process, info.thread, info.pid
            checked(api.AssignProcessToJobObject(self.job, self.process), "assign Job")
            if api.ResumeThread(thread) == 0xFFFFFFFF:
                raise OSError("Windows containment: resume failed")
        except BaseException:
            if self.process:
                api.TerminateProcess(self.process, 1)
                api.WaitForSingleObject(self.process, 5000)
            self.close()
            raise
        finally:
            if thread:
                api.CloseHandle(thread)
            if initialized:
                api.DeleteProcThreadAttributeList(attributes)
            if null and null != HANDLE(-1).value:
                api.CloseHandle(null)

    def poll(self) -> int | None:
        if self.returncode is None:
            outcome = self.api.WaitForSingleObject(self.process, 0)
            if outcome == 258:  # WAIT_TIMEOUT
                return None
            if outcome != 0:
                raise OSError("Windows containment: wait failed")
            code = DWORD()
            checked(self.api.GetExitCodeProcess(self.process, c.byref(code)), "read exit code")
            self.returncode = code.value
        return self.returncode

    def terminate(self) -> None:
        checked(self.api.TerminateJobObject(self.job, 1), "terminate Job")

    kill = terminate

    def wait(self) -> int:
        if self.api.WaitForSingleObject(self.process, 0xFFFFFFFF) != 0:
            raise OSError("Windows containment: wait failed")
        result = self.poll()
        assert result is not None
        return result

    def close(self) -> None:
        if self.job:
            self.api.CloseHandle(self.job)
            self.job = None
        if self.process:
            self.api.CloseHandle(self.process)
            self.process = None
