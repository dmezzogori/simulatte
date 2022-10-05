from __future__ import annotations

from typing import TYPE_CHECKING

from simpy.resources.store import Store

from simulatte import Job
from simulatte.simpy_extension import SequentialStore

if TYPE_CHECKING:
    from simpy import Environment


class WorkCenter:
    def __init__(self, env: Environment, input_queue: Store, output_queue: SequentialStore):
        self.env = env
        self.env.process(self.main())
        self._components = {}
        self._event_managers = {}
        self._input_queue = input_queue
        self._output_queue = output_queue

    def __call__(self, *components, event_manager):
        for component in components:
            self._components[component.__class__] = component
            if event_manager is not None:
                self._event_managers[component.__class__] = event_manager

    def _main_process(self, job: Job):
        raise NotImplementedError

    def _put_job(self, job):
        yield self._input_queue.put(job)

    def put(self, job):
        return self.env.process(self._put_job(job))

    def _get_job(self, job):
        yield self._output_queue.get(lambda j: j == job)

    def get(self, job):
        return self.env.process(self._get_job(job))

    def main(self):
        while True:
            next_job = yield self._input_queue.get()
            yield self.env.process(self._main_process(next_job))
            yield self._output_queue.put(next_job)
