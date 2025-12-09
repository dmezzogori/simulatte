from __future__ import annotations

from simulatte.utils.runner import Runner


def test_runner_sequential_generates_seeds_and_results():
    runner = Runner(n_jobs=3, seed=123)

    def worker(args):
        i, seed = args
        return (i, seed * 2)

    runner(worker, parallel=False)
    assert len(runner.results) == 3
    assert runner.results[0][0] == 0
    assert runner.seeds == [54, 275, 90]  # deterministic with seed=123


def test_runner_parallel_uses_pool_map_async(monkeypatch):
    calls: list[tuple[int, int]] = []

    class FakeAsyncResult:
        def __init__(self, data):
            self.data = data

        def wait(self):
            calls.extend(self.data)

        def get(self):
            return self.data

    class FakePool:
        def __init__(self, processes):
            self.processes = processes

        def map_async(self, worker, iterable, callback):
            data = [worker(item) for item in iterable]
            res = FakeAsyncResult(data)
            callback(data)
            return res

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("simulatte.utils.runner.Pool", FakePool)

    runner = Runner(n_jobs=2, seed=1)

    def worker(args):
        i, seed = args
        return (i, seed)

    runner(worker, parallel=True)
    assert runner.results == calls
    assert len(runner.results) == 2
