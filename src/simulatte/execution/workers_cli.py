"""``simulatte workers list|check|setup|prune``.

Exit 0 ready, 1 uv or cache missing, 2 unknown worker or unreachable.
"""

from __future__ import annotations

import dataclasses
import json
import sys

from simulatte.execution.workers import (
    WorkerCheck,
    WorkerSpec,
    check_worker,
    format_check,
    load_workers,
    prune_worker,
    resolve_workers,
    setup_worker,
)


WINDOWS_COORDINATOR_MESSAGE = "SSH workers from a Windows coordinator are untested in 0.17"
_WINDOWS_COORDINATOR = sys.platform == "win32"


def _noted(check: WorkerCheck) -> WorkerCheck:
    """Add the Windows-coordinator caveat to a check result (design 8): local experiments only."""
    if not _WINDOWS_COORDINATOR:
        return check
    return dataclasses.replace(check, messages=(*check.messages, WINDOWS_COORDINATOR_MESSAGE))


def add_workers_arguments(parser):
    commands = parser.add_subparsers(dest="workers_command", required=True)
    listing = commands.add_parser("list", help="List configured workers")
    listing.add_argument("--json", action="store_true")
    check = commands.add_parser("check", help="Check connectivity, platform, uv and cache on a worker")
    check.add_argument("name")
    check.add_argument("--json", action="store_true")
    setup = commands.add_parser("setup", help="Check a worker and optionally install uv on it")
    setup.add_argument("name")
    setup.add_argument("--install-uv", action="store_true", help="Offer to install uv with the official installer")
    setup.add_argument("--json", action="store_true")
    prune = commands.add_parser("prune", help="Remove cached blobs and environments no running attempt uses")
    prune.add_argument("name")
    prune.add_argument("--dry-run", action="store_true", help="List what would be removed")
    prune.add_argument("--json", action="store_true")


def _exit_code(check: WorkerCheck) -> int:
    if not check.reachable:
        return 2
    return 0 if check.uv_version is not None and check.cache_writable else 1


def _error(args, message: str) -> int:
    if args.json:
        print(json.dumps({"status": "validation_failed", "message": message[:4096]}, ensure_ascii=True))
    else:
        print(message[:4096], file=sys.stderr)
    return 2


def _envelope(spec: WorkerSpec, check: WorkerCheck) -> str:
    document = {"name": spec.name, "host": spec.host, **dataclasses.asdict(check)}
    return json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _stderr_confirm(prompt: str) -> str:
    # Keep stdout a single JSON document: the question goes to stderr, the answer is read bare.
    print(prompt, end="", file=sys.stderr, flush=True)
    return input("")


def workers(args) -> int:
    try:
        configured = load_workers()
        if args.workers_command == "list":
            if args.json:
                items = [{"name": w.name, "host": w.host, "slots": w.slots} for w in configured.values()]
                print(json.dumps({"status": "succeeded", "workers": items}, ensure_ascii=True, separators=(",", ":")))
            elif configured:
                for worker in configured.values():
                    print(f"{worker.name}  {worker.host}  {worker.slots}")
            else:
                print("No workers configured", file=sys.stderr)
            return 0
        (spec,) = resolve_workers([args.name], configured)
    except ValueError as error:
        return _error(args, str(error))
    if args.workers_command == "prune":
        reachable, removed, message = prune_worker(spec, dry_run=args.dry_run)
        if args.json:
            status = "succeeded" if reachable else "unreachable"
            document = {"status": status, "name": spec.name, "dry_run": args.dry_run, "removed": removed}
            print(json.dumps({**document, **({"message": message} if message else {})}, ensure_ascii=True))
        elif reachable:
            verb = "would remove" if args.dry_run else "removed"
            print("\n".join(f"{verb} {path}" for path in removed) or "nothing to remove")
        else:
            print(message, file=sys.stderr)
        return 0 if reachable else 2
    if args.workers_command == "check":
        check = _noted(check_worker(spec))
        if not args.json:
            print("\n".join(format_check(spec, check)))
    elif args.json:
        check = _noted(
            setup_worker(
                spec,
                install_uv=args.install_uv,
                confirm=_stderr_confirm,
                say=lambda line: print(line, file=sys.stderr),
            )
        )
    else:
        if _WINDOWS_COORDINATOR:
            print(WINDOWS_COORDINATOR_MESSAGE)
        check = _noted(setup_worker(spec, install_uv=args.install_uv, confirm=input))
    if args.json:
        print(_envelope(spec, check))
    return _exit_code(check)
