"""The periodic state push sends a peer a snapshot only when it differs from the last one that peer received."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")
from simulatte.studio.app import StudioServer


def test_state_is_pushed_only_when_it_changes():
    sent = []
    shared = SimpleNamespace(selected_view=None, sent_state=None)
    pinned = SimpleNamespace(selected_view="v", sent_state=None)
    snapshot = {"revision": 1}

    async def state(peer):
        return {**snapshot, "pinned": peer is not None}

    server = SimpleNamespace(
        hub=SimpleNamespace(connections=[shared, pinned], send=lambda *message: sent.append(message)),
        state=state,
    )

    def broadcast():
        asyncio.run(StudioServer._broadcast_state(server))

    broadcast()
    assert [(peer, kind) for peer, kind, _ in sent] == [(shared, "state"), (pinned, "state")]
    broadcast()
    broadcast()
    assert len(sent) == 2
    snapshot["revision"] = 2
    broadcast()
    assert [body for _, _, body in sent[2:]] == [{"revision": 2, "pinned": False}, {"revision": 2, "pinned": True}]
