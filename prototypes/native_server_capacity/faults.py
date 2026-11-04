"""Private ND_TEST_ALLOCATIONS images only; run under ASAN/UBSAN preload."""

import ctypes
import hashlib
import json
from pathlib import Path
import sys

import simulatte_native
from simulatte.experimental.native import Job, NativeStream


def main(output):
    directory = Path(simulatte_native.__file__).parent / "_bin"
    library = ctypes.CDLL(str(next(p for p in directory.glob("simulatte_native_engine.*") if p.is_file())))
    fail = library.nd_test_fail_allocation
    fail.argtypes = [ctypes.c_size_t]
    module = simulatte_native.load()
    rows = []
    for retention in ("full", "bounded"):
        configure = module.retention_configure_capacities if retention == "bounded" else module.configure_capacities
        for kind in ("configuration", "constructor", "admission", "advance", "projection"):
            failed = succeeded = 0
            for nth in (*range(1, 33), 0):
                stream = None
                try:
                    if kind == "constructor":
                        fail(nth)
                        stream = NativeStream(3, capacities=(2, 1, 3), retention=retention)
                    else:
                        stream = NativeStream(
                            3, capacities=None if kind == "configuration" else (2, 1, 3), retention=retention
                        )
                        if kind in ("advance", "projection"):
                            for i in range(40):
                                stream.add(Job(0, (i % 3, (i + 1) % 3), (0.5, 0.25), 2))
                        fail(nth)
                        if kind == "configuration":
                            configure(stream._engine, (2, 1, 3))
                        elif kind == "admission":
                            for i in range(40):
                                stream.add(Job(0, (0, 1), (0.5, 0.25), 2))
                        elif kind == "advance":
                            stream.advance(100)
                        else:
                            stream.state_projection()
                except MemoryError:
                    failed += 1
                    fail(0)
                    if stream is not None:
                        if kind == "projection":
                            view = stream.state_projection()
                            assert view["native_sha256"] == hashlib.sha256(stream.state_bytes()).hexdigest()
                            stream.advance(100)
                        else:
                            try:
                                stream.snapshot()
                            except RuntimeError:
                                pass
                            else:
                                raise AssertionError("mutating C failure left owner usable")
                else:
                    succeeded += 1
                finally:
                    fail(0)
                    if stream is not None:
                        stream.close()
            assert failed > 0 and succeeded > 0, (retention, kind, failed, succeeded)
            rows.append(dict(retention=retention, kind=kind, failed=failed, succeeded=succeeded))
        with NativeStream(3, capacities=(2, 1, 3), retention=retention) as healthy:
            healthy.add(Job(0, (0, 1), (0.5, 0.25), 2))
            healthy.run()
            assert healthy.storage_stats()["completed"] == 1
    output.write_text(json.dumps(dict(passed=True, rows=rows, python_leak_detection=False), indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
