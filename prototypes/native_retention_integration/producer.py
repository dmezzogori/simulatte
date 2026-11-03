"""Counter-based finite-lookahead input; no retained workload or RNG history."""

import struct
from simulatte.experimental.native import Job

ROUTES = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


class Producer:
    def __init__(self, seed=41, spacing=1.5):
        self.seed, self.spacing, self.cursor = seed, spacing, 0

    def next(self):
        i = self.cursor
        x = (i + self.seed * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        x ^= x >> 30
        x = (x * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        x ^= x >> 27
        arrival = i * self.spacing
        job = Job(
            arrival,
            ROUTES[x % 6],
            tuple((2 + (x >> (4 * k)) % 4) / 4 for k in range(3)),
            arrival + ((x >> 16) % 17 - 4),
        )
        self.cursor += 1
        return job

    def state(self):
        return dict(schema="counter-input-v1", seed=self.seed, spacing=self.spacing, cursor=self.cursor)


def encoded(job):
    return struct.pack("<dd3I3d", job.arrival, job.due, *job.route, *job.durations)
