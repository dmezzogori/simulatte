"""Private fault-enabled images: poison/retry rules, owner races and fresh recovery."""

import ctypes
import json
from pathlib import Path
import resource
import sys
import threading
import time

from simulatte.experimental.native import NativeStream, Job
import simulatte_native


def main(output):
    library = next((Path(simulatte_native.__file__).parent / "_bin").glob("simulatte_native_engine.*"))
    owner = ctypes.CDLL(str(library))
    fail = owner.nd_test_fail_allocation
    fail.argtypes = [ctypes.c_size_t]
    results = []
    for retention in ("full", "bounded"):
        for cycle in range(100):
            with NativeStream(1, retention=retention) as stream:
                stream.add(Job(0, (0,), (0.25,), 1))
                stream.advance(2)
                before = stream.state_bytes()
                fail(1)
                try:
                    stream.state_projection()
                except MemoryError:
                    pass
                else:
                    raise AssertionError("read-only allocation fault did not fire")
                finally:
                    fail(0)
                assert stream.state_bytes() == before
                # Invalid primitive input is recoverable before native mutation.
                try:
                    stream.advance(-1)
                except ValueError:
                    pass
                else:
                    raise AssertionError("invalid advance accepted")
                assert stream.state_bytes() == before
                while stream.storage_stats()["retained_jobs"] < stream.storage_stats()["job_capacity"]:
                    stream.add(Job(2, (0,), (0.25,), 4))
                fail(1)
                try:
                    stream.add(Job(2, (0,), (0.25,), 4))
                except MemoryError:
                    pass
                else:
                    raise AssertionError("mutating allocation fault did not fire")
                finally:
                    fail(0)
                for query in (stream.snapshot, stream.state_bytes, stream.state_projection, stream.storage_stats):
                    try:
                        query()
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("failed owner allowed state query")
            stream.close()
        results.append(
            dict(retention=retention, read_only_recoveries=100, invalid_input_recoveries=100, poisoned_owners=100)
        )

        with NativeStream(1, policy="spt", retention=retention) as stream:
            for index in range(16000):
                stream.add(Job(0, (0,), (0.25,), index + 1))
            errors = []

            def advance():
                try:
                    stream.advance(100000)
                except BaseException as error:
                    errors.append(str(error))

            thread = threading.Thread(target=advance)
            thread.start()
            raw = stream._module
            query = raw.retention_now if retention == "bounded" else raw.now
            close = raw.retention_close if retention == "bounded" else raw.close
            busy = False
            deadline = time.monotonic() + 5
            while thread.is_alive() and time.monotonic() < deadline:
                try:
                    query(stream._engine)
                except RuntimeError as error:
                    if "busy" in str(error):
                        busy = True
                        try:
                            close(stream._engine)
                        except RuntimeError as close_error:
                            assert "busy" in str(close_error)
                        else:
                            raise AssertionError("close raced native advance")
                        break
            assert busy
            thread.join(20)
            assert not thread.is_alive() and not errors, errors
            assert stream.storage_stats()["completed"] == 16000
        stream.close()
    output.write_text(
        json.dumps(
            dict(
                passed=True,
                results=results,
                busy_close_races=2,
                peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                python_leak_detection=False,
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
