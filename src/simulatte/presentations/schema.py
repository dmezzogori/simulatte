"""presentation-v1 manifests: validation with exact error paths, and the hashes shared with TypeScript.

:func:`validate_manifest` mirrors ``validatePresentation`` in
``studio/packages/playback/src/presentation-manifest.ts``. Both accept and reject the shared fixtures under
``tests/fixtures/presentations`` and name the same failing path: ``evidence[0].selection.rows.limit``, with the
document itself as ``""``. The published JSON Schema (:data:`MANIFEST_SCHEMA_PATH`) describes the same structure;
the rules it cannot express (ordered bounds and time range, unique ids, the selection allowed by each evidence
kind, the layout bundle) are checked here. References to stored recordings and analyses are checked by the store.

Every hash is :func:`simulatte.execution.hashing.canonical_hash` of :func:`json_value`, the value as JavaScript
reads its JSON text, so ``studio/packages/export/src/canonical.ts`` reproduces it byte for byte.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from pathlib import Path
import re
from typing import Any, NoReturn

from simulatte._wire import MAX_SAFE_INT
from simulatte.execution.hashing import canonical_hash

MANIFEST_SCHEMA_PATH = Path(__file__).parent / "schema" / "presentation-v1.schema.json"
SNAPSHOT_DOMAIN = "export-snapshot-v1"
MANIFEST_DOMAIN = "presentation-manifest-v1"
EVIDENCE_DOMAIN = "presentation-evidence-output-v1"
KINDS = ("number", "table", "distribution", "figure", "image")
LIMITS: dict[str, Any] = {
    "name": 200,
    "notes": 4000,
    "ids": 10_000,
    "measurements": 8,
    "annotations": 50,
    "annotation_text": 2000,
    "evidence": 12,
    "rows": 50,
    "columns": 12,
    "figure_width": (320, 3840),
    "figure_height": (180, 2160),
    "rate": 1024,
    "text": 1024,
}
MAX_DEPTH = 64
_KEYS = (
    "schema",
    "name",
    "notes",
    "recording",
    "layout",
    "camera",
    "visibility",
    "measurements",
    "time",
    "rate",
    "annotations",
    "evidence",
    "created",
)
_UUID = re.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX32 = re.compile("[0-9a-f]{32}")
_HEX64 = re.compile("[0-9a-f]{64}")
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ManifestError(ValueError):
    """A manifest rule failed at ``path`` (``""`` for the document)."""

    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


def json_value(value: Any, _depth: int = 0) -> Any:
    """A copy of JSON data as JavaScript reads it: integral floats within +/-(2**53 - 1) become ``int``.

    ``-0.0`` stays a float, as JavaScript packs it. Raises ``ValueError`` for non-finite numbers, integers outside
    the safe range, non-string keys, text that is not valid UTF-8, nesting deeper than 64 and other types.
    """
    if _depth > MAX_DEPTH:
        raise ValueError("value nests deeper than 64")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        value.encode("utf-8")
        return value
    if type(value) is int:
        if abs(value) > MAX_SAFE_INT:
            raise ValueError("integer outside +/-(2**53 - 1)")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("numbers must be finite")
        negative_zero = value == 0 and math.copysign(1, value) < 0
        return int(value) if value.is_integer() and abs(value) <= MAX_SAFE_INT and not negative_zero else value
    if isinstance(value, (list, tuple)):
        return [json_value(item, _depth + 1) for item in value]
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("object keys must be strings")
        return {key: json_value(item, _depth + 1) for key, item in value.items()}
    raise ValueError(f"not JSON data: {type(value).__name__}")


def snapshot_hash(manifest: Mapping, target: str, options: Mapping, statics: list) -> str:
    """The identity of what an export renders: the saved manifest, its target, options and static views."""
    value = {"manifest": manifest, "target": target, "options": options, "statics": statics}
    return canonical_hash(SNAPSHOT_DOMAIN, json_value(value))


def manifest_hash(manifest: Mapping) -> str:
    return canonical_hash(MANIFEST_DOMAIN, json_value(manifest))


def output_content_hash(descriptor: Mapping) -> str:
    """``EvidenceSelection.content_hash``: the hash of one plain ``OutputDescriptor`` of an output manifest."""
    return canonical_hash(EVIDENCE_DOMAIN, json_value(descriptor))


def _fail(path: str, message: str) -> NoReturn:
    raise ManifestError(path, message)


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _object(value: Any, path: str, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    for key in value:
        if key not in keys and key not in optional:
            _fail(_join(path, key), "unknown key")
    for key in keys:
        if key not in value:
            _fail(_join(path, key), "missing")
    return value


def _string(value: Any, path: str, low: int, high: int) -> str:
    if type(value) is not str:
        _fail(path, "must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(path, "must be valid UTF-8 text")
    if not low <= len(value) <= high or _CONTROL.search(value):
        _fail(path, f"must be plain text of {low} to {high} characters")
    return value


def _finite(value: Any) -> bool:
    if type(value) is int:
        return abs(value) <= MAX_SAFE_INT
    return type(value) is float and math.isfinite(value)


def _number(value: Any, path: str) -> float:
    if not _finite(value):
        _fail(path, "must be a finite number")
    return value


def _integer(value: Any, path: str, low: int, high: int = MAX_SAFE_INT) -> int:
    number = _number(value, path)
    if not float(number).is_integer() or not low <= number <= high:
        _fail(path, f"must be an integer from {low} to {high}")
    return int(number)


def _hex(value: Any, path: str, pattern: re.Pattern, what: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(path, f"must be a {what}")
    return value


def _uuid(value: Any, path: str) -> str:
    return _hex(value, path, _UUID, "UUID")


def _list(value: Any, path: str, high: int, what: str) -> list:
    if not isinstance(value, list) or len(value) > high:
        _fail(path, f"must list at most {high} {what}")
    return value


def _ids(value: Any, path: str) -> None:
    if value is None:
        return
    ids = [_string(item, f"{path}[{i}]", 1, LIMITS["text"]) for i, item in enumerate(_list(value, path, 10_000, "ids"))]
    if len(set(ids)) != len(ids):
        _fail(path, "ids must be unique")


def _labels(value: Any, path: str) -> None:
    record = _object(value, path, ("name", "unit", "population", "sample", "convention", "source"))
    sample = _object(record["sample"], f"{path}.sample", ("planned", "available", "partial"))
    if type(sample["partial"]) is not bool:
        _fail(f"{path}.sample.partial", "must be a boolean")
    source = _object(record["source"], f"{path}.source", ("analysis_name", "snapshot_hash", "execution_id"))
    text = LIMITS["text"]
    _string(record["name"], f"{path}.name", 0, text)
    for name in ("unit", "population"):
        if record[name] is not None:
            _string(record[name], f"{path}.{name}", 0, text)
    _integer(sample["planned"], f"{path}.sample.planned", 0)
    _integer(sample["available"], f"{path}.sample.available", 0)
    _string(record["convention"], f"{path}.convention", 0, text)
    _string(source["analysis_name"], f"{path}.source.analysis_name", 0, text)
    _hex(source["snapshot_hash"], f"{path}.source.snapshot_hash", _HEX64, "64-hex hash")
    _uuid(source["execution_id"], f"{path}.source.execution_id")


def _evidence(value: Any, path: str) -> str:
    record = _object(value, path, ("id", "execution_id", "output_id", "content_hash", "kind", "selection", "labels"))
    kind = record["kind"]
    if kind not in KINDS or type(kind) is not str:
        _fail(f"{path}.kind", f"must be one of {', '.join(KINDS)}")
    raw = record["selection"]
    if not isinstance(raw, dict):
        _fail(f"{path}.selection", "must be an object")
    if kind == "table":
        if list(raw) != ["rows"]:
            _fail(f"{path}.selection", "a table selects rows")
        where = f"{path}.selection.rows"
        rows = _object(raw["rows"], where, ("offset", "limit", "columns"))
        columns = _list(rows["columns"], f"{where}.columns", LIMITS["columns"], "columns")
        names = [_string(column, f"{where}.columns[{i}]", 1, 256) for i, column in enumerate(columns)]
        if len(set(names)) != len(names):
            _fail(f"{where}.columns", "columns must be unique")
        _integer(rows["offset"], f"{where}.offset", 0)
        _integer(rows["limit"], f"{where}.limit", 1, LIMITS["rows"])
    elif kind == "figure":
        if list(raw) != ["figure"]:
            _fail(f"{path}.selection", "a figure selects a static view size")
        where = f"{path}.selection.figure"
        figure = _object(raw["figure"], where, ("width", "height"))
        _integer(figure["width"], f"{where}.width", *LIMITS["figure_width"])
        _integer(figure["height"], f"{where}.height", *LIMITS["figure_height"])
    elif raw:
        _fail(f"{path}.selection", f"a {kind} output takes no selection")
    identity = _uuid(record["id"], f"{path}.id")
    _uuid(record["execution_id"], f"{path}.execution_id")
    _hex(record["output_id"], f"{path}.output_id", _HEX64, "64-hex output id")
    _hex(record["content_hash"], f"{path}.content_hash", _HEX64, "64-hex content hash")
    _labels(record["labels"], f"{path}.labels")
    return identity


def _layout(value: Any) -> None:
    from simulatte.layout.bundle import LayoutBundle

    try:
        if LayoutBundle.from_dict(value).schema_version != "1.0":
            raise ValueError('schema_version must be "1.0"')
        json_value(value)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        _fail("layout", str(error) or type(error).__name__)


def validate_manifest(document: Any) -> dict:
    """A normalized copy (see :func:`json_value`) of a valid manifest; ``ManifestError`` names the failing path.

    ``id`` and ``revision`` are optional here: a manifest sent to create a presentation has neither, or
    revision 0. A stored manifest always has both (``PresentationStore`` assigns them).
    """
    record = _object(document, "", _KEYS, ("id", "revision"))
    if type(record["schema"]) not in (int, float) or record["schema"] != 1:
        _fail("schema", "must be 1")
    recording = _object(record["recording"], "recording", ("attempt_id", "request_id", "trace_sha256"))
    _layout(record["layout"])
    camera = _object(record["camera"], "camera", ("bounds",))
    bounds = camera["bounds"]
    if (
        not isinstance(bounds, list)
        or len(bounds) != 4
        or not all(_finite(v) for v in bounds)
        or not (bounds[0] < bounds[2] and bounds[1] < bounds[3])
    ):
        _fail("camera.bounds", "must be four finite numbers [xmin, ymin, xmax, ymax] with xmin < xmax and ymin < ymax")
    visibility = _object(record["visibility"], "visibility", ("entities", "labels"))
    measurements = [
        _string(item, f"measurements[{i}]", 1, LIMITS["text"])
        for i, item in enumerate(
            _list(record["measurements"], "measurements", LIMITS["measurements"], "measurement keys")
        )
    ]
    if len(set(measurements)) != len(measurements):
        _fail("measurements", "keys must be unique")
    time = _object(record["time"], "time", ("start", "end"))
    if not _number(time["start"], "time.start") < _number(time["end"], "time.end"):
        _fail("time", "start must be before end")
    rate = _number(record["rate"], "rate")
    if not 0 < rate <= LIMITS["rate"]:
        _fail("rate", f"must be above 0 and at most {LIMITS['rate']}")
    annotations = _list(record["annotations"], "annotations", LIMITS["annotations"], "annotations")
    for i, item in enumerate(annotations):
        path = f"annotations[{i}]"
        annotation = _object(item, path, ("id", "text", "time"))
        _uuid(annotation["id"], f"{path}.id")
        _string(annotation["text"], f"{path}.text", 1, LIMITS["annotation_text"])
        if annotation["time"] is not None:
            _number(annotation["time"], f"{path}.time")
    evidence = _list(record["evidence"], "evidence", LIMITS["evidence"], "evidence selections")
    identities = [_evidence(item, f"evidence[{i}]") for i, item in enumerate(evidence)]
    if len(set(identities)) != len(identities):
        _fail("evidence", "ids must be unique")
    if "id" in record:
        _uuid(record["id"], "id")
    if "revision" in record:
        _integer(record["revision"], "revision", 0)
    _string(record["name"], "name", 1, LIMITS["name"])
    _string(record["notes"], "notes", 0, LIMITS["notes"])
    _hex(recording["attempt_id"], "recording.attempt_id", _HEX32, "32-hex attempt id")
    _hex(recording["request_id"], "recording.request_id", _HEX64, "64-hex request id")
    _hex(recording["trace_sha256"], "recording.trace_sha256", _HEX64, "64-hex trace hash")
    _ids(visibility["entities"], "visibility.entities")
    _ids(visibility["labels"], "visibility.labels")
    if _number(record["created"], "created") < 0:
        _fail("created", "must be epoch seconds")
    return json_value(record)
