"""Installed integration fixture; all three historical consumers remain frozen."""

import os

from simulatte.experimental.native_identity import compose_native_identity

if __package__:
    from . import (
        reference_projection as reference,
        prepared_projection as prepared,
        unprepared_projection as unprepared,
    )
else:
    import reference_projection as reference
    import prepared_projection as prepared
    import unprepared_projection as unprepared

Actions = reference.Actions
SET_SCHEMA = reference.SET_SCHEMA
validate = reference.validate
require_reproduction = reference.require_reproduction


def capture(backend, input_state, actions):
    mode = os.environ.get("SIMULATTE_PROJECTION_MODE", "helper")
    if mode == "prepared":
        return prepared.hook_capture(backend, actions)
    if mode == "unprepared":
        return unprepared.hook_capture(backend, actions)
    if mode == "reference":
        return reference.capture(backend, input_state, actions)
    if mode != "helper":
        raise ValueError("Unsupported integration mode")
    value = compose_native_identity(backend.state_projection(), actions=actions)
    return value.to_dict(), dict(raw_bytes=value.native_bytes, view_bytes=value.size_bytes)
