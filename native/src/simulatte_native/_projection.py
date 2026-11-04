"""Versioned provider projection; native layouts remain owned by their serializers."""

import hashlib
import math

from simulatte._wire import canonical_pack, freeze


def project(module, owner, retention, history):
    if retention not in ("full", "bounded"):
        raise ValueError("projection retention must be full or bounded")
    bounded = retention == "bounded"
    if bounded and (type(history) is not bytes or len(history) != 32):
        raise ValueError("bounded projection requires the owner's completion-history digest")
    query = getattr(module, "retention_state_projection" if bounded else "state_projection", None)
    if not callable(query):
        raise ImportError("Rebuild/install the matching native provider with state projection v1")
    value = query(owner)
    codecs = ("NBST0001", "NBST0002") if bounded else ("NSTA0001", "NSTA0002")
    if (
        type(value) is not dict
        or set(value) != {"schema", "codec", "native", "snapshot"}
        or value["schema"] != "native-provider-raw-projection-v1"
        or value["codec"] not in codecs
        or type(value["native"]) is not bytes
        or not value["native"].startswith(value["codec"].encode("ascii"))
    ):
        raise RuntimeError("Unsupported native provider projection")
    codec = value["codec"]
    snapshot = value["snapshot"]
    if type(snapshot) is not dict or set(snapshot) != {"window", "scalars"}:
        raise RuntimeError("Invalid native projection snapshot")
    window, scalars = snapshot["window"], snapshot["scalars"]
    if (
        type(window) is not tuple
        or len(window) != 3
        or type(window[2]) is not bool
        or any(type(v) is not float or not math.isfinite(v) for v in window[:2])
        or type(scalars) is not dict
        or any(type(k) is not str or type(v) is not float or not math.isfinite(v) for k, v in scalars.items())
    ):
        raise RuntimeError("Invalid native projection values")
    raw = value["native"]
    physical = hashlib.sha256(raw).hexdigest()
    trajectory = dict(codec=codec, physical_sha256=physical)
    state_hash, size = physical, len(raw)
    if bounded:
        trajectory = dict(codec=codec + "+completion-history-v1", history_sha256=history.hex(), live_sha256=physical)
        # Exact versioned NRST commitment, without parsing or joining blobs.
        state = hashlib.sha256(b"NRST0002" if codec == "NBST0002" else b"NRST0001")
        state.update(history)
        state.update(len(raw).to_bytes(8, "little"))
        state.update(raw)
        tail = canonical_pack(freeze(snapshot))
        state.update(tail)
        state_hash, size = state.hexdigest(), 48 + len(raw) + len(tail)
    return dict(
        schema="native-stream-projection-v1",
        trajectory=trajectory,
        snapshot=snapshot,
        native_sha256=state_hash,
        native_bytes=size,
    )
