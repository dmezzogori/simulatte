"""Compose model identities using the public hook; optional frozen oracle checks."""

import os

if __package__:
    from . import reference_projection as reference
else:
    import reference_projection as reference

Actions = reference.Actions
SET_SCHEMA = reference.SET_SCHEMA
digest = reference.digest
validate = reference.validate
require_reproduction = reference.require_reproduction


def hook_capture(backend, actions):
    view = backend.state_projection()
    if view["schema"] != "native-production-projection-v1":
        raise ValueError("Unsupported managed projection")
    scope, authority = view["scope"], view["authority"]
    trajectory = dict(schema=reference.TRAJECTORY, scope=scope, native=view["trajectory"], actions=actions)
    report = dict(schema=reference.REPORTING, scope=scope, **view["reporting"])
    t = dict(schema=reference.TRAJECTORY, digest=reference.digest(trajectory))
    r = dict(schema=reference.REPORTING, digest=reference.digest(report))
    complete = dict(schema=reference.COMPLETE, scope=scope, authority=authority, trajectory_control=t, reporting=r)
    result = dict(
        schema=SET_SCHEMA,
        trajectory_control=t,
        reporting=r,
        reproduction_state=dict(schema=reference.COMPLETE, digest=reference.digest(complete)),
        authority=authority,
        evidence=dict(
            trajectory=trajectory, report=report, reproduction=complete, authority_preimage=view["authority_preimage"]
        ),
    )
    result = reference.plain(reference.freeze(result))
    return result, dict(raw_bytes=view["native_bytes"], view_bytes=len(reference.pack(result)))


def capture(backend, input_state, actions):
    mode = os.environ.get("SIMULATTE_PROJECTION_MODE", "hook")
    if mode == "reference":
        return reference.capture(backend, input_state, actions)
    if mode not in ("hook", "compare"):
        raise ValueError("Unsupported projection experiment mode")
    result = hook_capture(backend, actions)
    if mode == "compare":
        oracle = reference.capture(backend, input_state, actions)
        assert result == oracle, "Public hook differs from frozen parser projection"
    return result
