"""Experimental per-query canonical preparation; fresh public hook and old validator."""

import hashlib
import os

from simulatte._wire import canonical_pack, freeze

if __package__:
    from . import reference_projection as reference, unprepared_projection as unprepared
else:
    import reference_projection as reference
    import unprepared_projection as unprepared

Actions = reference.Actions
SET_SCHEMA = reference.SET_SCHEMA
digest = reference.digest
validate = reference.validate
require_reproduction = reference.require_reproduction


def component(schema, prepared):
    return freeze(dict(schema=schema, digest=hashlib.sha256(canonical_pack(prepared)).hexdigest()))


def prepare(view, actions):
    if view["schema"] != "native-production-projection-v1":
        raise ValueError("Unsupported managed projection")
    view, actions = freeze(view), freeze(actions)
    scope, authority, reporting = view["scope"], view["authority"], view["reporting"]
    trajectory = freeze(dict(schema=reference.TRAJECTORY, scope=scope, native=view["trajectory"], actions=actions))
    report = freeze(
        dict(
            schema=reference.REPORTING,
            scope=scope,
            configuration=reporting["configuration"],
            catalog=reporting["catalog"],
            snapshot=reporting["snapshot"],
        )
    )
    t, r = component(reference.TRAJECTORY, trajectory), component(reference.REPORTING, report)
    complete = freeze(
        dict(schema=reference.COMPLETE, scope=scope, authority=authority, trajectory_control=t, reporting=r)
    )
    return freeze(
        dict(
            schema=SET_SCHEMA,
            trajectory_control=t,
            reporting=r,
            reproduction_state=component(reference.COMPLETE, complete),
            authority=authority,
            evidence=dict(
                trajectory=trajectory,
                report=report,
                reproduction=complete,
                authority_preimage=view["authority_preimage"],
            ),
        )
    )


def hook_capture(backend, actions):
    # Exactly one fresh public observation. Canonical values live for this query only.
    view = backend.state_projection()
    prepared = prepare(view, actions)
    size = len(canonical_pack(prepared))
    result = reference.plain(prepared)
    return result, dict(raw_bytes=view["native_bytes"], view_bytes=size)


def capture(backend, input_state, actions):
    mode = os.environ.get("SIMULATTE_PROJECTION_MODE", "hook")
    if mode == "unprepared":
        return unprepared.hook_capture(backend, actions)
    if mode == "reference":
        return reference.capture(backend, input_state, actions)
    if mode not in ("hook", "compare"):
        raise ValueError("Unsupported projection experiment mode")
    result = hook_capture(backend, actions)
    if mode == "compare":
        baseline = unprepared.hook_capture(backend, actions)
        oracle = reference.capture(backend, input_state, actions)
        assert result == baseline == oracle
        assert reference.pack(result[0]) == reference.pack(baseline[0]) == reference.pack(oracle[0])
    return result
