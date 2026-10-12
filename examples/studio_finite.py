"""Finite arrivals drain explicitly, independently of the event queue.

uv run simulatte run examples/studio_finite.py --arrivals 8 --safety-limit 100
uv run simulatte run examples/studio_finite.py --cutoff 4 --safety-limit 100

The execution driver supplies the selected stopping policy to Population(env).
Ordinary scripts can pass their own FinitePopulation policy to Population.
"""

from dataclasses import dataclass
from typing import Annotated, ClassVar

import simpy

from simulatte import FinitePopulation, Population, Range, model
from simulatte.entities import Entity, FieldSpec, StateSchema
from simulatte.events import Deltas, DomainEvent, event_type


class Machine(Entity, kind="studio_example_machine"):
    state_schema: ClassVar[StateSchema] = StateSchema({"busy": FieldSpec("bool"), "completed": FieldSpec("int")})

    def __init__(self, env):
        self.busy = False
        self.completed = 0
        env.entities.attach(self, name="machine")


@event_type("studio_example.machine_changed", touches={"studio_example_machine": ("busy", "completed")})
class MachineChanged(DomainEvent):
    machine: str
    busy: bool
    completed: int


@dataclass(frozen=True)
class Params:
    interarrival: Annotated[float, Range(0.01, 100)] = 0.5
    service: Annotated[float, Range(0.01, 100)] = 1.0


@model(params=Params, stop=FinitePopulation(arrivals=5, safety_limit=1000))
def build(env, layout, params):
    population = Population(env)
    machine = Machine(env)
    resource = simpy.Resource(env, capacity=1)

    def changed(busy):
        machine.busy = busy
        if not busy:
            machine.completed += 1
        if env.wants(MachineChanged):
            env.emit(
                MachineChanged(
                    machine=machine.id,
                    busy=machine.busy,
                    completed=machine.completed,
                    deltas=Deltas.build()
                    .set(machine.id, "busy", machine.busy)
                    .set(machine.id, "completed", machine.completed)
                    .done(),
                )
            )

    def process(key):
        with resource.request() as request:
            yield request
            changed(True)
            yield env.timeout(params.service)
            changed(False)
        population.complete(key)

    def arrivals():
        index = 0
        while population.open:
            key = f"arrival-{index}"
            population.pending(key)
            yield env.timeout(params.interarrival) | population.closed
            population.unpending(key)
            if not population.open:
                return
            if population.admit(key):
                env.process(process(key))
            index += 1

    env.process(arrivals())
