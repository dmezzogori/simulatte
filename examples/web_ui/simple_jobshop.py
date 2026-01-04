"""Simple job shop simulation example for the Simulatte Web UI.

Run with:
    simulatte examples/web_ui/simple_jobshop.py
"""

from __future__ import annotations

import random

from simulatte.environment import Environment
from simulatte.job import ProductionJob
from simulatte.server import Server
from simulatte.shopfloor import ShopFloor
from simulatte.web import SimulationComponents


class Simulation:
    """A simple job shop simulation with 6 servers and random job arrivals."""

    def setup(self, env: Environment) -> SimulationComponents:
        """Configure the simulation components."""
        self.env = env
        random.seed(42)

        # Create shopfloor with time-series collection for analytics
        self.shopfloor = ShopFloor(env=env, collect_time_series=True)

        # Create 6 servers (processing stations)
        self.servers = tuple(
            Server(
                env=env,
                capacity=1,
                shopfloor=self.shopfloor,
                collect_time_series=True,
            )
            for _ in range(6)
        )

        # Start job generation process
        env.process(self._generate_jobs())

        return SimulationComponents(
            shopfloor=self.shopfloor,
            servers=self.servers,
            server_names={i: f"Station {i + 1}" for i in range(6)},
        )

    def _generate_jobs(self):
        """Generate jobs with random routing and processing times."""
        job_count = 0
        skus = ["Widget-A", "Widget-B", "Gadget-X", "Part-123", "Assembly-Z"]

        while True:
            # Random inter-arrival time (exponential with mean 2)
            yield self.env.timeout(random.expovariate(0.5))

            # Random SKU
            sku = random.choice(skus)

            # Random routing: 2-4 servers from the 6 available
            num_servers = random.randint(2, 4)
            routing_servers = tuple(random.sample(self.servers, num_servers))

            # Random processing times (exponential with mean 3)
            processing_times = [random.expovariate(1 / 3) for _ in range(num_servers)]

            # Due date: current time + random offset (30-60)
            due_date = self.env.now + random.uniform(30, 60)

            # Create job
            job = ProductionJob(
                env=self.env,
                sku=sku,
                servers=routing_servers,
                processing_times=processing_times,
                due_date=due_date,
            )

            # Add to shopfloor
            self.shopfloor.add(job)
            job_count += 1

    def run(self, until: float) -> None:
        """Run the simulation."""
        self.env.run(until=until)
