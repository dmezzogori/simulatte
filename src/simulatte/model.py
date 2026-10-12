"""Optional model metadata and data-only parameter schemas for local execution.

Decorators return their function unchanged. Annotation evaluation and dataclass
construction belong in the process loading the model, never a Studio server.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
import math
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeVar, cast, get_args, get_origin, get_type_hints

from simulatte._wire import MAX_SAFE_INT

F = TypeVar("F", bound=Callable[..., Any])
Scalar = str | int | float | bool
_TYPES = {"bool": bool, "int": int, "float": float, "str": str}


def _number(value: Any) -> bool:
    return (type(value) is int and abs(value) <= MAX_SAFE_INT) or (type(value) is float and math.isfinite(value))


@dataclass(frozen=True, slots=True)
class Range:
    """Inclusive numeric bounds."""

    min: float | None = None
    max: float | None = None

    def __post_init__(self):
        if any(x is not None and not _number(x) for x in (self.min, self.max)):
            raise ValueError("Range bounds must be finite safe numbers")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Range minimum exceeds maximum")


@dataclass(frozen=True, slots=True)
class Length:
    """Inclusive string length bounds, measured in Unicode code points."""

    min: int = 0
    max: int | None = None

    def __post_init__(self):
        if type(self.min) is not int or not 0 <= self.min <= MAX_SAFE_INT:
            raise ValueError("Length minimum must be a nonnegative safe integer")
        if self.max is not None and (type(self.max) is not int or not self.min <= self.max <= MAX_SAFE_INT):
            raise ValueError("Length maximum must be an integer at least the minimum")


@dataclass(frozen=True, slots=True)
class Description:
    text: str

    def __post_init__(self):
        if type(self.text) is not str or len(self.text.encode("utf-8")) > 65536:
            raise ValueError("Description must be text of at most 64 KiB")


@dataclass(frozen=True, slots=True)
class ParameterField:
    name: str
    type: str
    required: bool
    default: Scalar | None = None
    choices: tuple[Scalar, ...] = ()
    range: Range | None = None
    length: Length | None = None
    description: str = ""

    def __post_init__(self):
        if type(self.name) is not str or not self.name.isidentifier() or self.type not in (*_TYPES, "enum"):
            raise ValueError("Invalid parameter name or type")
        if type(self.required) is not bool or type(self.choices) is not tuple:
            raise ValueError("Invalid parameter required flag or choices")
        Description(self.description)
        if self.range is not None and (type(self.range) is not Range or self.type not in ("int", "float")):
            raise ValueError("Range requires a numeric parameter")
        if self.length is not None and (type(self.length) is not Length or self.type != "str"):
            raise ValueError("Length requires a string parameter")
        if self.type == "enum" and not self.choices:
            raise ValueError("Enum needs choices")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("Duplicate parameter choices")
        for choice in self.choices:
            self.validate(choice, check_choices=False)
        if self.required:
            if self.default is not None:
                raise ValueError("Required field has a default")
        else:
            self.validate(self.default)

    def validate(self, value: Any, *, check_choices: bool = True) -> Scalar:
        expected = str if self.type == "enum" else _TYPES[self.type]
        if expected is float and type(value) is int and abs(value) <= MAX_SAFE_INT:
            value = float(value)
        if type(value) is not expected:
            raise ValueError(f"{self.name}: expected {self.type}")
        if expected is int and abs(value) > MAX_SAFE_INT:
            raise ValueError(f"{self.name}: integer exceeds safe range")
        if expected is float:
            if not math.isfinite(value):
                raise ValueError(f"{self.name}: float must be finite")
            if value == 0:
                value = 0.0
        if expected is str and len(cast(str, value).encode("utf-8")) > 65536:
            raise ValueError(f"{self.name}: string exceeds 64 KiB")
        if check_choices and self.choices and value not in self.choices:
            raise ValueError(f"{self.name}: not an allowed choice")
        if self.range and (
            (self.range.min is not None and value < self.range.min)
            or (self.range.max is not None and value > self.range.max)
        ):
            raise ValueError(f"{self.name}: outside allowed range")
        if self.length and (
            len(cast(str, value)) < self.length.min
            or (self.length.max is not None and len(cast(str, value)) > self.length.max)
        ):
            raise ValueError(f"{self.name}: outside allowed length")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "description": self.description,
            "range": None if self.range is None else {"min": self.range.min, "max": self.range.max},
            "length": None if self.length is None else {"min": self.length.min, "max": self.length.max},
        }


@dataclass(frozen=True, slots=True)
class ParameterSchema:
    fields: tuple[ParameterField, ...]

    def __post_init__(self):
        if (
            type(self.fields) is not tuple
            or len(self.fields) > 256
            or any(type(f) is not ParameterField for f in self.fields)
        ):
            raise ValueError("Invalid parameter fields (maximum 256)")
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Duplicate parameter field")

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "fields": [f.to_dict() for f in self.fields]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ParameterSchema:
        if set(data) != {"version", "fields"} or type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Unsupported parameter schema")
        if not isinstance(data["fields"], (list, tuple)) or len(data["fields"]) > 256:
            raise ValueError("Invalid parameter fields")
        result = []
        try:
            for item in data["fields"]:
                if set(item) != {"name", "type", "required", "default", "choices", "range", "length", "description"}:
                    raise ValueError("Invalid parameter field keys")
                if not isinstance(item["choices"], (list, tuple)):
                    raise ValueError("Invalid parameter choices")
                result.append(
                    ParameterField(
                        name=item["name"],
                        type=item["type"],
                        required=item["required"],
                        default=item["default"],
                        choices=tuple(item["choices"]),
                        description=item["description"],
                        range=Range(**item["range"]) if item["range"] is not None else None,
                        length=Length(**item["length"]) if item["length"] is not None else None,
                    )
                )
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid parameter schema") from error
        return cls(tuple(result))


def _annotations(params: type) -> dict[str, Any]:
    try:
        return get_type_hints(params, include_extras=True)
    except (NameError, TypeError) as error:
        raise ValueError(f"Cannot resolve Params annotations: {error}") from error


def _schema(params: type) -> ParameterSchema:
    if not isinstance(params, type) or not is_dataclass(params) or not getattr(params, "__dataclass_params__").frozen:
        raise ValueError("params must be a frozen dataclass type")
    hints = _annotations(params)
    result = []
    for field in fields(params):
        if not field.init or field.default_factory is not MISSING:
            raise ValueError(f"{field.name}: init=False and default factories are unsupported")
        annotation = hints[field.name]
        metadata = ()
        if get_origin(annotation) is Annotated:
            annotation, *metadata = get_args(annotation)
        constraints: dict[str, Any] = {}
        for constraint in metadata:
            key = {Range: "range", Length: "length", Description: "description"}.get(type(constraint))
            if key is None or key in constraints:
                raise ValueError(f"{field.name}: unknown or duplicate constraint")
            constraints[key] = constraint.text if isinstance(constraint, Description) else constraint
        choices: tuple[Scalar, ...] = ()
        default = field.default if field.default is not MISSING else None
        if get_origin(annotation) is Literal:
            choices = get_args(annotation)
            if not choices or len({type(x) for x in choices}) != 1:
                raise ValueError(f"{field.name}: mixed Literal choices")
            annotation = type(choices[0])
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            members = annotation.__members__
            if not members or len(set(members.values())) != len(members):
                raise ValueError(f"{field.name}: empty or aliased Enum")
            if any(
                type(member.value) not in (str, bool, int, float)
                or (type(member.value) in (int, float) and not _number(member.value))
                for member in members.values()
            ):
                raise ValueError(f"{field.name}: Enum values must be finite scalars")
            choices = tuple(members)
            if default is not None:
                if type(default) is not annotation:
                    raise ValueError(f"{field.name}: Enum default must be a member")
                default = default.name
            kind = "enum"
        else:
            kind = next((name for name, type_ in _TYPES.items() if annotation is type_), "")
            if not kind:
                raise ValueError(f"{field.name}: unsupported parameter annotation")
        result.append(ParameterField(field.name, kind, field.default is MISSING, default, choices, **constraints))
    return ParameterSchema(tuple(result))


@dataclass(frozen=True, slots=True)
class ModelSpec:
    params: type
    horizon: float | None
    warmup: float
    time_unit: str | None
    inputs: tuple[str, ...]
    stop: Any
    schema: ParameterSchema
    name: str
    description: str


def model(
    *,
    params: type,
    horizon: float | None = None,
    warmup: float = 0,
    time_unit: str | None = None,
    inputs: Iterable[str] = (),
    stop: Any = None,
) -> Callable[[F], F]:
    """Describe a build function without wrapping it or changing ordinary calls."""
    if (horizon is None) == (stop is None):
        raise ValueError("Specify exactly one of horizon or stop")
    if not _number(warmup) or warmup < 0:
        raise ValueError("warmup must be finite and nonnegative")
    if horizon is not None and (not _number(horizon) or horizon <= 0 or warmup >= horizon):
        raise ValueError("Require finite horizon > warmup >= 0")
    if stop is not None:
        from simulatte.stopping import FinitePopulation, Horizon

        if type(stop) not in (FinitePopulation, Horizon) or warmup != 0:
            raise ValueError("Invalid stop policy or duplicate warmup")
    if time_unit is not None and (type(time_unit) is not str or not time_unit or len(time_unit) > 256):
        raise ValueError("time_unit must be nonempty text of at most 256 characters")
    if isinstance(inputs, (str, bytes)):
        raise ValueError("inputs must be a sequence of relative paths")
    captured = tuple(inputs)
    for path in captured:
        if (
            type(path) is not str
            or not path
            or "\\" in path
            or ":" in path
            or "\0" in path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path
            or path == "."
        ):
            raise ValueError("inputs must be normalized project-relative file paths")
    if len(set(captured)) != len(captured):
        raise ValueError("Duplicate declared inputs")
    schema = _schema(params)

    def decorate(build: F) -> F:
        if not callable(build) or hasattr(build, "__simulatte_model__"):
            raise ValueError("Model must be an undecorated callable")
        spec = ModelSpec(
            params,
            float(horizon) if horizon is not None else None,
            float(warmup),
            time_unit,
            captured,
            stop,
            schema,
            getattr(build, "__name__", type(build).__name__),
            build.__doc__ or "",
        )
        setattr(build, "__simulatte_model__", spec)
        return build

    return decorate


def get_model_spec(build: Callable[..., Any]) -> ModelSpec:
    spec = getattr(build, "__simulatte_model__", None)
    if not isinstance(spec, ModelSpec):
        raise ValueError("Function is not a decorated Simulatte model")
    return spec


def parameter_schema(spec: ModelSpec) -> ParameterSchema:
    return spec.schema


def validate_parameters(schema: ParameterSchema, values: Mapping[str, Any]) -> dict[str, Scalar]:
    unknown = set(values) - {f.name for f in schema.fields}
    if unknown:
        raise ValueError("Unknown parameter")
    result = {}
    for field in schema.fields:
        if field.name not in values and field.required:
            raise ValueError(f"{field.name}: required parameter missing")
        result[field.name] = field.validate(values.get(field.name, field.default))
    return result


def parse_parameters(schema: ParameterSchema, raw: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, Scalar]:
    pairs = raw.items() if isinstance(raw, Mapping) else raw
    values: dict[str, Any] = {}
    known = {f.name: f for f in schema.fields}
    for name, value in pairs:
        if name not in known or name in values:
            raise ValueError("Unknown or duplicate parameter")
        if type(value) is not str or len(value.encode("utf-8")) > 65536:
            raise ValueError(f"{name}: invalid parameter text")
        kind = known[name].type
        try:
            if kind == "bool":
                if value not in ("true", "false"):
                    raise ValueError()
                parsed = value == "true"
            elif kind == "int":
                if (
                    not value
                    or not value.removeprefix("-").removeprefix("+").isascii()
                    or not value.removeprefix("-").removeprefix("+").isdigit()
                ):
                    raise ValueError()
                parsed = int(value)
            elif kind == "float":
                if "_" in value or value.strip() != value:
                    raise ValueError()
                parsed = float(value)
            else:
                parsed = value
        except ValueError as error:
            raise ValueError(f"{name}: invalid {kind}") from error
        values[name] = parsed
    return validate_parameters(schema, values)


def construct_parameters(spec: ModelSpec, values: Mapping[str, Any]) -> Any:
    """Construct Params in the execution child, including its cross-field checks."""
    normalized: dict[str, Any] = validate_parameters(spec.schema, values)
    hints = _annotations(spec.params)
    for field in spec.schema.fields:
        if field.type == "enum":
            annotation = hints[field.name]
            if get_origin(annotation) is Annotated:
                annotation = get_args(annotation)[0]
            normalized[field.name] = annotation[normalized[field.name]]
    return spec.params(**normalized)
