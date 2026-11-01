"""A bounded Python controller; future job contents require a live observation."""

from prototypes.native_engine.model import Job


def feed(stream, templates, producer):
    if producer == "independent":
        for job in templates.jobs:
            stream.advance(job.arrival)
            stream.add(job)
        return templates.jobs, ()
    if producer != "feedback":
        raise ValueError("unknown producer")
    jobs, handles, decisions = [], [], []
    for index, base in enumerate(templates.jobs):
        stream.advance(base.arrival)
        if index:
            probe = max(0, index - 1 - index % 32)
            snapshot = stream.inspect(handles[probe])
            state = snapshot["state"]
            choice = "short" if state == "pool" else "long" if state in ("scheduled", "active") else "normal"
            decisions.append((probe, snapshot["arrival"], snapshot["released"], snapshot["finished"], state, choice))
        else:
            choice = "seed"
            decisions.append((None, None, None, None, None, choice))
        if choice == "short":
            length = max(1, len(base.route) // 2)
            route = base.route[::-1][:length]
            durations = tuple(duration * 0.75 for duration in base.durations[::-1][:length])
        elif choice == "long":
            route, durations = base.route, tuple(duration * 1.125 for duration in base.durations)
        else:
            route, durations = base.route, base.durations
        # Construction and its validation happen only after the observation.
        job = Job(base.arrival, route, durations, base.due)
        handles.append(stream.add(job))
        jobs.append(job)
    return tuple(jobs), tuple(decisions)


def validate_feed(templates, jobs, log, result):
    """Reconstruct observations from final trajectories using strict advance(t)."""
    assert len(templates.jobs) == len(jobs) == len(log)
    for index, (base, job, row) in enumerate(zip(templates.jobs, jobs, log)):
        if index == 0:
            assert row == (None, None, None, None, None, "seed")
            choice = "seed"
        else:
            probe = max(0, index - 1 - index % 32)
            arrival = jobs[probe].arrival
            released, finished = result["released"][probe], result["finished"][probe]
            at = base.arrival
            state = (
                "scheduled" if arrival >= at else "finished" if finished < at else "active" if released < at else "pool"
            )
            choice = "short" if state == "pool" else "long" if state in ("scheduled", "active") else "normal"
            assert row == (
                probe,
                arrival,
                released if state in ("active", "finished") else None,
                finished if state == "finished" else None,
                state,
                choice,
            )
        assert job.arrival == base.arrival and job.due == base.due
        if choice == "short":
            # Separate construction from the producer implementation above.
            indices = list(range(len(base.route) - 1, -1, -1))[: max(1, len(base.route) // 2)]
            assert job.route == tuple(base.route[i] for i in indices)
            assert job.durations == tuple(0.75 * base.durations[i] for i in indices)
        else:
            assert job.route == base.route
            factor = 1.125 if choice == "long" else 1.0
            assert job.durations == tuple(factor * d for d in base.durations)
