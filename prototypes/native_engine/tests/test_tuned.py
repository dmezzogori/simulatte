import random

import pytest

from prototypes.native_engine.model import Job, Workload, fifo_hook, spt_hook
from prototypes.native_engine.reference import ReferenceRunner
from prototypes.native_engine.tuned import TunedRunner
from prototypes.native_engine.validation import compare_results


@pytest.mark.parametrize("policy,hook", [("fifo", None), ("spt", None), ("fifo", fifo_hook), ("spt", spt_hook)])
def test_queue_specialization_preserves_ties(tmp_path, policy, hook):
    for seed in range(100):
        rng = random.Random(seed)
        jobs = []
        for _ in range(30):
            route = rng.sample(range(4), rng.randint(1, 4))
            jobs.append(Job(rng.randrange(5), tuple(route), tuple(rng.randint(1, 3) for _ in route), 10))
        workload = Workload(4, tuple(sorted(jobs, key=lambda job: job.arrival)))
        results, traces = [], []
        for cls in (ReferenceRunner, TunedRunner):
            path = tmp_path / cls.__name__
            runner = cls(workload, policy=policy, hook=hook, mode="record", trace_path=path)
            runner.run()
            results.append(runner.result())
            runner.close()
            traces.append(path.read_bytes())
        compare_results(*results)
        assert traces[0] == traces[1]
