"""Headless CLI adapter. Model code is imported only by execution children."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any

from simulatte.execution.coordinator import Coordinator, ExecutionFailure
from simulatte.execution.schema import _plain
from simulatte.execution.snapshot import discover_project
from simulatte.model import ParameterSchema, parse_parameters
from simulatte.stopping import Horizon, FinitePopulation, stopping_from_dict


def add_arguments(parser, *, headless=True):
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
    if headless:
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
            state = coordinator.inputs.bind(prepared)
            state = coordinator.inputs.apply(
                state.revision,
                {
                    "parameters": parameters,
                    "seed": args.seed,
                    "stopping": _stopping(args, model["stopping"]),
                    "level": args.level,
                    "kpis": tuple(args.kpi),
                },
                auto_run=False,
            )
            assert state.request is not None
            key = coordinator.submit(state.request, state.revision, timeout=args.timeout)
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


def add_experiment_arguments(parser):
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="Run an experiment file to completion")
    run.add_argument("file")
    run.add_argument("--workers", help="comma-separated worker names (default: the file's, else local)")
    status = actions.add_parser("status", help="Show one run, or the newest runs")
    status.add_argument("run_id", nargs="?")
    for name, text in (
        ("cancel", "Cancel a paused run"),
        ("resume", "Resume a paused run"),
        ("delete", "Delete a run"),
    ):
        actions.add_parser(name, help=text).add_argument("run_id")
    results = actions.add_parser("results", help="Show per-configuration intervals and paired comparisons")
    results.add_argument("run_id")
    results.add_argument("--level", type=float, default=0.95, help="confidence level (default: 0.95)")
    results.add_argument("--baseline", help="configuration label to compare the others against, paired by replication")
    results.add_argument("--bonferroni", action="store_true", help="adjust the level for the number of intervals shown")
    for name, action in actions.choices.items():
        action.add_argument("--project", help="project directory holding the .simulatte store")
        action.add_argument("--json", action="store_true")
        if name in ("run", "resume"):
            action.add_argument(
                "--local-slots", type=int, help="local experiment slots (default: 4, at most cpu count - 1)"
            )
            action.add_argument("--timeout", type=float, help="bound on preparation and on each replication, seconds")


def add_replay_arguments(parser):
    parser.add_argument("request_id")
    parser.add_argument("--project", help="project directory holding the .simulatte store")
    parser.add_argument("--worker", help="worker to replay on (default: the original attempt's; local for local)")
    parser.add_argument("--timeout", type=float, help="bound on the replay attempt, seconds")
    parser.add_argument("--json", action="store_true")


def replay(args):
    """``simulatte replay REQUEST_ID``: exit 0 when verified, 1 when unverified or failed, 2 for usage."""
    coordinator = None
    envelope: dict[str, Any] = {"request_id": args.request_id}
    try:
        coordinator = Coordinator.from_store(Path(args.project or "."), args.request_id)
        outcome = coordinator.replay(args.request_id, worker=args.worker, timeout=args.timeout)
        record = coordinator.store.replay(outcome.key.request_id)
        envelope = {
            "status": "succeeded",
            "request_id": outcome.key.request_id,
            "original": record["original"],
            "attempt_id": outcome.key.attempt_id,
            "verified": outcome.verification.verified,
            "reason": outcome.verification.reason,
            "path": str(coordinator.store.result(outcome.key.attempt_id).path),
        }
        code = 0 if outcome.verification.verified else 1
    except KeyboardInterrupt:
        code, envelope = 130, {"status": "cancelled", "message": "Cancelled by user"}
    except ExecutionFailure as error:
        code, envelope = 1, {**envelope, "status": error.status, "message": str(error)[:4096]}
        if error.key is not None:
            envelope.update(request_id=error.key.request_id, attempt_id=error.key.attempt_id)
    except (ValueError, KeyError) as error:
        code, envelope = 2, {"status": "validation_failed", "message": str(error)[:4096]}
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
    elif "message" in envelope:
        print(envelope["message"], file=sys.stderr)
    else:
        label = "verified" if envelope["verified"] else f"unverified ({envelope['reason']})"
        print(
            f"{label}: replay {envelope['request_id']} of {envelope['original']} "
            f"attempt {envelope['attempt_id']} at {envelope['path']}",
            file=sys.stderr,
        )
    return code


def add_store_arguments(parser):
    actions = parser.add_subparsers(dest="action", required=True)
    prune = actions.add_parser("prune", help="Remove unreferenced results and snapshots")
    prune.add_argument("--dry-run", action="store_true")
    prune.add_argument(
        "--recordings",
        action="store_true",
        help="also drop presentation recordings no presentation adopted, beyond the newest 20",
    )
    check = actions.add_parser(
        "check", help="Verify saved traces and artifacts against their recorded hashes, read-only"
    )
    for command in (prune, check):
        command.add_argument("--project", help="project directory holding the .simulatte store")
        command.add_argument("--json", action="store_true")


_RUN_EXIT = {"completed": 0, "failed": 1, "cancelled": 130, "paused": 130}


def _store_root(args):
    root = Path(args.project or ".").resolve()
    if not (root / ".simulatte" / "index.sqlite").is_file():
        raise ValueError(f"No run store in {root}")
    return root


def _slots(args):
    from simulatte.execution.slots import SlotConfig

    return SlotConfig() if args.local_slots is None else SlotConfig(local=args.local_slots)


def _table(report):
    from simulatte.execution.scheduler import REPLICATION_STATES

    lines = [
        f"{report['name']} {report['run_id']} {report['status']}",
        "\t".join(("configuration", *REPLICATION_STATES)),
    ]
    for item in report["configurations"]:
        flag = "\tmixed_runtime" if item["mixed_runtime"] else ""
        lines.append("\t".join((item["label"], *(str(item["counts"][state]) for state in REPLICATION_STATES))) + flag)
    return "\n".join(lines)


def _results_text(document):
    from tabulate import tabulate

    def number(value):
        return "-" if value is None else f"{value:.6g}"

    def cells(item):
        estimate = item["estimate"]
        return [
            estimate["n"],
            *(number(estimate[name]) for name in ("mean", "half_width", "lower", "upper")),
            estimate["state"],
        ]

    percent = f"{document['effective_level'] * 100:.4g} %"
    if document["pointwise"]:
        label = f"pointwise {percent} t intervals"
    else:
        label = (
            f"Bonferroni-adjusted t intervals, {document['level'] * 100:.4g} % family level "
            f"({percent} each, {document['m']} intervals)"
        )
    interval = ["n", "mean", "half-width", "lower", "upper", "state"]
    lines = [f"{document['name']} {document['run_id']} {document['status']}", label]
    rows = [[item["label"], item["kpi"], item["n_succeeded"], *cells(item)] for item in document["rows"]]
    lines.append(tabulate(rows, ["configuration", "kpi", "succeeded", *interval], tablefmt="simple"))
    if "paired" in document:
        percent = f"{document['paired_effective_level'] * 100:.4g} %"
        kind = "pointwise" if document["pointwise"] else f"Bonferroni-adjusted ({document['paired_m']} comparisons)"
        lines += [
            "",
            f"paired differences, configuration - {document['baseline']}: {kind} {percent} t intervals",
        ]
        rows = [
            [item["label"], item["kpi"], *cells(item), ",".join(map(str, item["missing"]))]
            for item in document["paired"]
        ]
        lines.append(tabulate(rows, ["configuration", "kpi", *interval, "missing"], tablefmt="simple"))
    if document["missing"]:
        lines += ["", f"replications without a result ({document['missing_total']}):"]
        rows = [
            [item["configuration"], item["index"], item["status"], item["attempt"] or "-"]
            for item in document["missing"]
        ]
        lines.append(tabulate(rows, ["configuration", "index", "status", "attempt"], tablefmt="simple"))
    return "\n".join(lines)


def _emit_result(args, code, envelope, text):
    if args.json:
        print(json.dumps(_plain(envelope), ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    elif code == 0 and text is not None:
        print(text)
    else:
        print(envelope.get("message") or text or envelope["status"], file=sys.stderr)
    return code


def experiment(args):
    """``simulatte experiment run|status|results|cancel|resume|delete``; exit codes as ``simulatte run``."""
    from simulatte.execution import scheduler
    from simulatte.execution.snapshot import ProjectSpec
    from simulatte.execution.store import RunStore

    coordinator = None
    run_id = None
    code, text = 0, None
    try:
        if args.action in ("run", "resume"):
            if args.action == "run":
                from simulatte.experiments import load_experiment
                from simulatte.experiments.runner import project_root

                if not Path(args.file).is_file():
                    raise ValueError(f"Experiment file not found: {args.file}")
                definition = load_experiment(args.file)
                root = project_root(args.file, args.project)
                spec = discover_project(root / definition.model.partition(":")[0], root)
                coordinator = Coordinator(spec, reference=definition.model, slots=_slots(args))
                workers = None if args.workers is None else [name.strip() for name in args.workers.split(",")]
                run_id = coordinator.start_experiment(definition, workers=workers, timeout=args.timeout)
                for warning in coordinator.preflight_warnings:  # e.g. private_index:<url> (the host's credentials)
                    print(f"warning: {warning}", file=sys.stderr)
            else:
                root = _store_root(args)
                with RunStore.open(root / ".simulatte") as store:
                    preparation = store.experiment_run(args.run_id)["preparation"]
                project: Any = {**preparation["project"], "root": root}
                coordinator = Coordinator(
                    ProjectSpec(**project), reference=preparation["request"]["model"], slots=_slots(args)
                )
                run_id = args.run_id
                coordinator.resume_experiment(run_id, timeout=args.timeout)
            envelope = coordinator.wait_experiment(run_id)
            code, text = _RUN_EXIT.get(envelope["status"], 1), _table(envelope)
        else:
            with RunStore.open(_store_root(args) / ".simulatte") as store:
                if args.action == "status" and args.run_id is None:
                    listed = scheduler.runs(store)
                    envelope = {"status": "succeeded", "runs": listed}
                    text = "\n".join(f"{item['run_id']} {item['status']} {item['name']}" for item in listed)
                elif args.action == "status":
                    envelope = scheduler.report(store, args.run_id)
                    text = _table(envelope)
                elif args.action == "results":
                    from simulatte.experiments.results import ExperimentReport

                    envelope = ExperimentReport(store, args.run_id).to_dict(
                        args.level, baseline=args.baseline, bonferroni=args.bonferroni
                    )
                    text = _results_text(envelope)
                elif args.action == "cancel":
                    # The store was just recovered: no replication of a paused run is dispatched.
                    scheduler.cancel_run(store, args.run_id)
                    envelope = {"status": "cancelled", "run_id": args.run_id}
                    text = f"cancelled {args.run_id}"
                else:
                    removed = store.delete_experiment_run(args.run_id)["results"]
                    envelope = {"status": "succeeded", "run_id": args.run_id, "results": removed}
                    text = f"deleted {args.run_id}: {len(removed)} results removed"
    except KeyboardInterrupt:
        code = 130
        envelope = (
            {"status": "cancelled", "message": "Cancelled by user"}
            if run_id is None
            else {
                "status": "paused",
                "run_id": run_id,
                "message": f"Interrupted; resume with experiment resume {run_id}",
            }
        )
    except ExecutionFailure as error:
        code, envelope = 1, {"status": error.status, "message": str(error)[:4096], "run_id": run_id}
    except (ValueError, KeyError) as error:
        code, envelope = 2, {"status": "validation_failed", "message": str(error)[:4096]}
        if type(getattr(error, "code", None)) is str:  # PreflightError: missing_lock, incompatible_simulatte...
            envelope["code"] = getattr(error, "code")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    finally:
        if coordinator is not None:
            try:
                coordinator.close()
            except (OSError, RuntimeError, sqlite3.Error) as error:
                code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    return _emit_result(args, code, envelope, text)


def store(args):
    """``simulatte store prune [--dry-run] [--recordings]`` and ``simulatte store check``.

    A dry run also lists, under ``protected``, every kept result with the owner chains that keep it.
    ``check`` opens the store read-only (a copied project needs no writer) and exits 1 when an indexed
    file is missing, unsafe or changed.
    """
    from simulatte.execution.store import RunStore

    if args.action == "check":
        return _check(args)
    try:
        with RunStore.open(_store_root(args) / ".simulatte") as owner:
            pruned = owner.prune(dry_run=args.dry_run, recordings=args.recordings, explain=args.dry_run)
        code = 0
        envelope = {"status": "succeeded", "dry_run": args.dry_run, **pruned}
    except (ValueError, KeyError) as error:
        code, envelope = 2, {"status": "validation_failed", "message": str(error)[:4096]}
    except (OSError, RuntimeError, sqlite3.Error) as error:
        code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    verb = "would remove" if args.dry_run else "removed"
    results, snapshots = len(envelope.get("results", [])), len(envelope.get("snapshots", []))
    text = f"{verb} {results} results and {snapshots} snapshots"
    if "recordings" in envelope:
        text = f"{verb} {results} results, {snapshots} snapshots and {len(envelope['recordings'])} recordings"
    if "protected" in envelope:
        text += f"; {len(envelope['protected'])} results kept (--json names the owners keeping each)"
    return _emit_result(args, code, envelope, text)


def _check(args):
    from simulatte.execution.store_reader import StoreReader

    text = None
    try:
        reader = StoreReader.open(_store_root(args) / ".simulatte")
        try:
            problems = reader.check()
            with reader._transaction() as db:
                results = db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
                analyses = (
                    db.execute("SELECT COUNT(*) FROM analysis_outputs").fetchone()[0] if reader.version >= 4 else 0
                )
        finally:
            reader.close()
        code = 1 if problems else 0
        envelope = {"status": "failed" if problems else "succeeded", "problems": problems}
        text = "\n".join(f"{item['problem']} {item['path']} (sha256 {item['sha256']})" for item in problems)
        if not problems:
            text = f"verified {results} results and {analyses} analyses"
    except (ValueError, KeyError) as error:
        code, envelope = 2, {"status": "validation_failed", "message": str(error)[:4096]}
    except (OSError, sqlite3.Error) as error:
        code, envelope = 1, {"status": "failed", "message": str(error)[:4096]}
    return _emit_result(args, code, envelope, text)
