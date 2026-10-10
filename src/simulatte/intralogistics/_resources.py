"""SimPy resources that report every change of their state (spec §6.4).

SimPy completes a waiting request or get inside the callback of a later event (the release or put that makes room),
before the waiting process resumes. Events emitted by the waiting process would therefore come after other events
of the same instant, and the replayed state would lag the live one in between. Like servers (spec §6.3), these
resources report each change from the place where SimPy makes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

if TYPE_CHECKING:
    from collections.abc import Callable

    from simpy.core import Environment as SimpyEnvironment
    from simpy.resources.container import ContainerAmount, ContainerGet, ContainerPut
    from simpy.resources.resource import Release, Request


class NotifyingResource(simpy.Resource):
    """A ``simpy.Resource`` that calls ``on_change(request, granted)`` after each change of ``users``.

    `granted` is True when `request` was granted a slot and False when its slot was released; releasing a request
    that holds no slot changes nothing and calls nothing.
    """

    def __init__(self, env: SimpyEnvironment, capacity: int = 1, *, on_change: Callable[[Request, bool], None]) -> None:
        super().__init__(env, capacity)
        self._on_change = on_change

    def _do_put(self, event: Request) -> None:
        users = self.users
        before = len(users)
        super()._do_put(event)
        if len(users) != before:
            self._on_change(event, True)

    def _do_get(self, event: Release) -> None:
        users = self.users
        before = len(users)
        super()._do_get(event)
        if len(users) != before:
            self._on_change(event.request, False)


class NotifyingContainer(simpy.Container):
    """A ``simpy.Container`` that calls ``on_change(container, amount)`` after each completed put (`amount` > 0) and
    get (`amount` < 0).

    The constructor accepts the arguments of ``simpy.Container``; without `on_change` it reports nothing.
    """

    def __init__(
        self,
        env: SimpyEnvironment,
        capacity: ContainerAmount = float("inf"),
        init: ContainerAmount = 0,
        *,
        on_change: Callable[[NotifyingContainer, ContainerAmount], None] | None = None,
    ) -> None:
        super().__init__(env, capacity, init)
        self._on_change = on_change

    def _do_put(self, event: ContainerPut) -> bool | None:
        done = super()._do_put(event)
        if done and self._on_change is not None:
            self._on_change(self, event.amount)
        return done

    def _do_get(self, event: ContainerGet) -> bool | None:
        done = super()._do_get(event)
        if done and self._on_change is not None:
            self._on_change(self, -event.amount)
        return done
