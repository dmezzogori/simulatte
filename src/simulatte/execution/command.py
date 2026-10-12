"""Headless CLI adapter. Model code is imported only by execution children."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.model import ParameterSchema, parse_parameters
from simulatte.stopping import Horizon, FinitePopulation, stopping_from_dict


def add_arguments(parser):
    parser.add_argument("model", nargs="?")
    parser.add_argument("--project")
    parser.add_argument("--layout", default="layout.json")
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--seed", type=int, default=0)
    stop = parser.add_mutually_exclusive_group()
    stop.add_argument("--horizon", type=float)
    stop.add_argument("--arrivals", type=int)
    stop.add_argument("--cutoff", type=float)
    parser.add_argument("--warmup", type=float)
    parser.add_argument("--safety-limit", type=float)
    parser.add_argument("--level", choices=("full", "kpi"), default="full")
    parser.add_argument("--kpi", action="append", default=[])
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--reproduce")


def _stopping(args, default):
    policy = dict(default)
    if args.horizon is not None:
        policy = Horizon(args.horizon, args.warmup or 0).to_dict()
    elif args.arrivals is not None or args.cutoff is not None:
        safety = args.safety_limit if args.safety_limit is not None else policy.get("safety_limit")
        if safety is None:
            raise ValueError("Finite population stopping requires --safety-limit")
        policy = FinitePopulation(arrivals=args.arrivals, cutoff=args.cutoff, safety_limit=safety).to_dict()
    if args.warmup is not None:
        if policy["type"] != "horizon":
            raise ValueError("warmup requires horizon stopping")
        policy["warmup"] = args.warmup
    if args.safety_limit is not None:
        if policy["type"] != "finite_population":
            raise ValueError("safety-limit requires finite population stopping")
        policy["safety_limit"] = args.safety_limit
    return stopping_from_dict(policy).to_dict()


def _export(source, destination):
    # Exclusive creation closes the check/create race; delete only our partial
    # output on failure. No overwrite flag exists.
    with destination.open("xb") as output:
        try:
            with source.open("rb") as trace:
                shutil.copyfileobj(trace, output, 1 << 20)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            destination.unlink()
            raise


def run(args):
    coordinator = None
    key = None
    envelope = {}
    code = 0
    reproducing = False
    try:
        if bool(args.model) == bool(args.reproduce):
            raise ValueError("Specify a model or --reproduce REQUEST_ID")
        if args.output is not None and (args.output.exists() or args.output.is_symlink()):
            raise ValueError("Output already exists")
        if args.reproduce:
            if (
                args.param
                or args.seed
                or args.kpi
                or args.level != "full"
                or any(
                    value is not None
                    for value in (args.horizon, args.arrivals, args.cutoff, args.warmup, args.safety_limit)
                )
                or args.layout != "layout.json"
            ):
                raise ValueError("Reproduction uses stored inputs; execution overrides are unavailable")
            reproducing = True
            coordinator = Coordinator.from_store(Path(args.project or "."), args.reproduce)
            result = coordinator.reproduce(args.reproduce, timeout=args.timeout)
        else:
            # Split only the optional suffix after .py, preserving Windows drive letters.
            path, separator, name = args.model.rpartition(":")
            selected = bool(separator and path.endswith(".py"))
            spec = discover_project(path if selected else args.model, args.project, layout=args.layout)
            reference = spec.model + (":" + name if selected else "")
            coordinator = Coordinator(spec, reference=reference)
            prepared = coordinator.prepare(timeout=args.timeout)
            model = next(item for item in prepared.metadata["models"] if item["name"] == prepared.metadata["selected"])
            raw = []
            for item in args.param:
                name, separator, value = item.partition("=")
                if not separator:
                    raise ValueError("Parameters use NAME=VALUE")
                raw.append((name, value))
            parameters = parse_parameters(ParameterSchema.from_dict(model["parameters"]), raw)
            request = coordinator.make_request(
                prepared,
                parameters=parameters,
                seed=args.seed,
                stopping=_stopping(args, model["stopping"]),
                level=args.level,
                kpis=tuple(args.kpi),
            )
            key = coordinator.submit(request, prepared.revision, timeout=args.timeout)
            result = coordinator.wait(key)
        key = result.key
        if args.output is not None:
            _export(result.path / "run.simtrace", args.output)
        envelope = {
            "status": "succeeded",
            "request_id": key.request_id,
            "attempt_id": key.attempt_id,
            "path": str(result.path),
            "trace": str(args.output or result.path / "run.simtrace"),
            "reproduced": bool(args.reproduce),
        }
    except KeyboardInterrupt:
        if coordinator is not None and key is not None:
            coordinator.cancel(key)
        code, envelope = 130, {"status": "cancelled", "message": "Cancelled by user"}
    except ExecutionFailure as error:
        code = 2 if error.status == "validation_failed" else 130 if error.status == "cancelled" else 1
        envelope = {
            "status": error.status,
            "message": str(error)[:4096],
            "request_id": error.key.request_id,
            "attempt_id": error.key.attempt_id,
        }
    except (ValueError, KeyError) as error:
        code = 1 if reproducing else 2
        envelope = {"status": "failed" if reproducing else "validation_failed", "message": str(error)[:4096]}
    except (OSError, RuntimeError, sqlite3.Error) as error:
        code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    finally:
        if coordinator is not None:
            try:
                coordinator.close()
            except (OSError, RuntimeError, sqlite3.Error) as error:
                code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    if args.json:
        print(json.dumps(_plain(envelope), ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    else:
        print(
            envelope.get(
                "message",
                f"{envelope['status']}: request {envelope.get('request_id', '')} "
                f"attempt {envelope.get('attempt_id', '')} at {envelope.get('path', '')}",
            ),
            file=sys.stderr,
        )
    return code
