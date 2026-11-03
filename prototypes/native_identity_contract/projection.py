"""Research-only native identity layers; original backend state remains authority."""

import hashlib
import math
import re
import struct

from simulatte._wire import canonical_pack, freeze, FrozenMap

SET_SCHEMA = "native-identity-set-v1"
TRAJECTORY = "native-trajectory-control-v1"
REPORTING = "native-reporting-state-v1"
COMPLETE = "native-reproduction-state-v1"
ACTIONS = "admission-actuation-v1"
HEX = re.compile(r"[0-9a-f]{64}")


def pack(value):
    return canonical_pack(freeze(value))


def digest(value):
    return hashlib.sha256(pack(value)).hexdigest()


def plain(value):
    if isinstance(value, FrozenMap):
        return {k: plain(v) for k, v in value.items()}
    if type(value) is tuple:
        return [plain(v) for v in value]
    return value


class Actions:
    def __init__(self):
        self.hash = hashlib.sha256((ACTIONS + "\0").encode())
        self.rows = 0

    def update(self, at, tick, target, jobs):
        value = pack(dict(t=float(at), tick=tick, target=target, jobs=jobs))
        self.hash.update(len(value).to_bytes(8, "little"))
        self.hash.update(value)
        self.rows += 1

    def state(self):
        return dict(schema=ACTIONS, rows=self.rows, digest=self.hash.hexdigest())


def _full(raw):
    if len(raw) < 108:
        raise ValueError("Truncated NSTA0001 header")
    servers, cap, policy, state, jobs, ops, pending = struct.unpack_from("<7I", raw, 8)
    seq, floor, dispatched, completed, events, heap, pool = struct.unpack_from("<7Q", raw, 44)
    expected = 108 + servers * 12 + jobs * 36 + (jobs + 1) * 4 + ops * 44 + dispatched * 4 + pool * 4 + heap * 24
    if not servers or policy > 1 or state not in (0, 2) or pending > 1 or expected != len(raw):
        raise ValueError("Invalid NSTA0001 shape")
    return dict(codec="NSTA0001", physical_sha256=hashlib.sha256(raw).hexdigest())


def _bounded_body(body):
    if len(body) < 100 or bytes(body[:8]) != b"NBST0001":
        raise ValueError("Invalid NBST0001 header")
    servers, cap, policy, state, pending = struct.unpack_from("<5I", body, 8)
    live = struct.unpack_from("<I", body, 96)[0]
    if not servers or policy > 1 or state not in (0, 2) or pending > 1:
        raise ValueError("Invalid NBST0001 configuration")
    offset = 100 + servers * 28
    for _ in range(live):
        if offset + 48 > len(body):
            raise ValueError("Truncated NBST0001 job")
        job_state, count = struct.unpack_from("<2I", body, offset + 40)
        if job_state > 2 or not 0 < count <= servers:
            raise ValueError("Invalid NBST0001 job")
        offset += 48 + count * 48
    if offset + 8 > len(body):
        raise ValueError("Truncated NBST0001 pool")
    pool = struct.unpack_from("<Q", body, offset)[0]
    offset += 8 + pool * 8
    if pool > live or offset + 8 > len(body):
        raise ValueError("Invalid NBST0001 pool")
    heap = struct.unpack_from("<Q", body, offset)[0]
    offset += 8 + heap * 32
    if offset != len(body):
        raise ValueError("Invalid NBST0001 calendar length")


def native_material(raw, snapshot):
    if type(raw) is not bytes:
        raise ValueError("Exact native bytes required")
    if raw.startswith(b"NSTA0001"):
        return _full(raw)
    if not raw.startswith(b"NRST0001") or len(raw) < 56:
        raise ValueError("Unsupported/truncated native encoding")
    length = struct.unpack_from("<Q", raw, 40)[0]
    if length > len(raw) - 48:
        raise ValueError("Truncated NRST0001 body")
    body = memoryview(raw)[48 : 48 + length]
    _bounded_body(body)
    if raw[48 + length :] != pack(snapshot):
        raise ValueError("NRST0001 KPI tail differs from canonical snapshot")
    return dict(
        codec="NBST0001+completion-history-v1",
        history_sha256=raw[8:40].hex(),
        live_sha256=hashlib.sha256(body).hexdigest(),
    )


def _snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != {"window", "scalars"}:
        raise ValueError("Invalid stock snapshot")
    window = snapshot["window"]
    if type(window) not in (tuple, list) or len(window) != 3 or type(window[2]) is not bool:
        raise ValueError("Invalid stock window")
    if any(type(v) is not float or not math.isfinite(v) for v in window[:2]):
        raise ValueError("Invalid stock clock")
    if type(snapshot["scalars"]) is not dict or any(
        type(k) is not str or type(v) is not float or not math.isfinite(v) for k, v in snapshot["scalars"].items()
    ):
        raise ValueError("Invalid stock scalar values")


def assemble(raw, snapshot, inputs, authority, scope, report_config, catalog, actions):
    _snapshot(snapshot)
    if set(authority) != {"schema", "digest"} or not HEX.fullmatch(authority["digest"]):
        raise ValueError("Invalid authority")
    expected = digest(dict(schema=authority["schema"], native=hashlib.sha256(raw).hexdigest(), inputs=inputs))
    if expected != authority["digest"]:
        raise ValueError("Native authority does not bind raw state/callback")
    schema = "native-production-v1" if raw.startswith(b"NSTA0001") else "native-production-bounded-v1"
    if authority["schema"] != schema:
        raise ValueError("Native authority schema differs from encoding")
    if (
        actions.get("schema") != ACTIONS
        or type(actions.get("rows")) is not int
        or actions["rows"] < 0
        or not HEX.fullmatch(actions.get("digest", ""))
    ):
        raise ValueError("Invalid action commitment")
    trajectory = dict(schema=TRAJECTORY, scope=scope, native=native_material(raw, snapshot), actions=actions)
    report = dict(schema=REPORTING, scope=scope, configuration=report_config, catalog=catalog, snapshot=snapshot)
    t = dict(schema=TRAJECTORY, digest=digest(trajectory))
    r = dict(schema=REPORTING, digest=digest(report))
    reproduction = dict(schema=COMPLETE, scope=scope, authority=authority, trajectory_control=t, reporting=r)
    result = dict(
        schema=SET_SCHEMA,
        trajectory_control=t,
        reporting=r,
        reproduction_state=dict(schema=COMPLETE, digest=digest(reproduction)),
        authority=authority,
        evidence=dict(
            trajectory=trajectory,
            report=report,
            reproduction=reproduction,
            authority_preimage=dict(schema=authority["schema"], native=hashlib.sha256(raw).hexdigest(), inputs=inputs),
        ),
    )
    return plain(freeze(result))


def capture(backend, input_state, actions):
    # Research access to the existing owner/lock, not a new public backend API.
    stream = backend._stream
    if stream is None:
        raise RuntimeError("Unprepared backend")
    with stream._lock:
        backend._synchronized()
        before = freeze(input_state()) if input_state is not None else None
        raw = stream.state_bytes()
        snapshot = stream.snapshot()
        authority = dict(schema=backend.capabilities.state_schema, digest=backend.state_digest())
        after = freeze(input_state()) if input_state is not None else None
        if (
            pack(before) != pack(after)
            or hashlib.sha256(stream.state_bytes()).digest() != hashlib.sha256(raw).digest()
            or pack(stream.snapshot()) != pack(snapshot)
        ):
            raise ValueError("Impure or inconsistent identity snapshot")
        scope = dict(
            backend=backend.capabilities.name, scope=backend._context.scope, time_unit=backend._context.env.time_unit
        )
        catalog = [
            dict(name=k.name, unit=k.unit, observation=k.observation, aggregation=k.aggregation) for k in backend.kpis
        ]
        value = assemble(raw, snapshot, before, authority, scope, backend._context.stopping.to_dict(), catalog, actions)
        return value, dict(raw_bytes=len(raw), view_bytes=len(pack(value)))


def validate(value):
    if value.get("schema") != SET_SCHEMA:
        raise ValueError("Unsupported identity-set schema")
    evidence = value["evidence"]
    for field, payload, schema in (
        ("trajectory_control", "trajectory", TRAJECTORY),
        ("reporting", "report", REPORTING),
        ("reproduction_state", "reproduction", COMPLETE),
    ):
        if (
            value[field]["schema"] != schema
            or evidence[payload]["schema"] != schema
            or value[field]["digest"] != digest(evidence[payload])
        ):
            raise ValueError("Invalid identity component: " + field)
    expected = evidence["reproduction"]
    if (
        expected["authority"] != value["authority"]
        or expected["trajectory_control"] != value["trajectory_control"]
        or expected["reporting"] != value["reporting"]
    ):
        raise ValueError("Identity set was spliced")
    preimage = evidence["authority_preimage"]
    if preimage["schema"] != value["authority"]["schema"] or digest(preimage) != value["authority"]["digest"]:
        raise ValueError("Original state authority mismatch")
    trajectory, report = evidence["trajectory"], evidence["report"]
    if trajectory["scope"] != report["scope"] or trajectory["scope"] != expected["scope"]:
        raise ValueError("Identity scopes differ")
    _snapshot(report["snapshot"])
    material = trajectory["native"]
    if material["codec"] == "NSTA0001":
        valid = (
            value["authority"]["schema"] == "native-production-v1" and material["physical_sha256"] == preimage["native"]
        )
    elif material["codec"] == "NBST0001+completion-history-v1":
        valid = value["authority"]["schema"] == "native-production-bounded-v1" and all(
            HEX.fullmatch(material[k]) for k in ("history_sha256", "live_sha256")
        )
    else:
        valid = False
    if not valid:
        raise ValueError("Invalid trajectory encoding/authority")


def require_reproduction(first, second):
    validate(first)
    validate(second)
    if first["reproduction_state"] != second["reproduction_state"] or first["authority"] != second["authority"]:
        raise ValueError("Complete reproduction identity differs")
