"""Lint for the emission guard pattern (spec §7.1, §18).

Every ``env.emit(...)`` of a domain event in the package must be guarded by ``env.wants(...)``, so that event
arguments are evaluated only when someone listens. Accepted guards, from the enclosing statements of the call:

- an ``if`` whose test calls ``wants(...)`` or reads a name bound from an expression that calls it
  (``wants = env.wants(JobGranted)`` ... ``if wants:``; ``before = ... if env.wants(X) else None`` ...
  ``if before is not None:``), with the call in its body;
- an earlier ``if`` in an enclosing block whose test calls ``wants(...)`` (or reads such a name) and whose body
  ends with ``return`` (``if not env.wants(X): return``).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import simulatte
from simulatte.events import DomainEvent

PACKAGE = Path(simulatte.__file__).parent


def _calls_wants(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "wants" for n in ast.walk(node)
    )


def _wants_names(function: ast.AST) -> set[str]:
    """Names assigned, anywhere in `function`, from an expression that calls ``wants(...)``."""
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and _calls_wants(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _calls_wants(node.value):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _guards(test: ast.AST, names: set[str]) -> bool:
    return _calls_wants(test) or any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(test))


def _is_env_emit(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "emit"):
        return False
    receiver = node.func.value
    return (isinstance(receiver, ast.Name) and receiver.id == "env") or (
        isinstance(receiver, ast.Attribute) and receiver.attr in ("env", "_env")
    )


def _event_class(call: ast.Call, namespace: dict[str, object]) -> object:
    if call.args and isinstance(call.args[0], ast.Call) and isinstance(call.args[0].func, ast.Name):
        return namespace.get(call.args[0].func.id)
    return None


def unguarded_emits(source: str, namespace: dict[str, object]) -> list[int]:
    """Line numbers of ``env.emit(...)`` calls of domain events (or unresolvable events) without a guard."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    found: list[int] = []
    for node in ast.walk(tree):
        if not _is_env_emit(node):
            continue
        assert isinstance(node, ast.Call)
        cls = _event_class(node, namespace)
        if isinstance(cls, type) and not issubclass(cls, DomainEvent):
            continue
        chain = [node]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        function = next((n for n in chain if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), tree)
        names = _wants_names(function)
        guarded = False
        for child, parent in zip(chain, chain[1:], strict=False):
            if isinstance(parent, ast.If) and child in parent.body and _guards(parent.test, names):
                guarded = True
                break
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if isinstance(block, list) and child in block:
                    for earlier in block[: block.index(child)]:
                        if (
                            isinstance(earlier, ast.If)
                            and _guards(earlier.test, names)
                            and earlier.body
                            and isinstance(earlier.body[-1], ast.Return)
                        ):
                            guarded = True
            if guarded:
                break
        if not guarded:
            found.append(node.lineno)
    return found


def test_every_domain_event_emit_in_the_package_is_guarded() -> None:
    offenders: list[str] = []
    checked = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if ".emit(" not in source:
            continue
        module = importlib.import_module(
            "simulatte." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts).removesuffix(".__init__")
        )
        checked += source.count(".emit(")
        offenders += [f"{path.relative_to(PACKAGE)}:{line}" for line in unguarded_emits(source, vars(module))]
    assert checked >= 10  # the scan found the emitting sites
    assert offenders == []


def test_the_lint_reports_unguarded_and_accepts_each_guard_form() -> None:
    namespace: dict[str, object] = {"Ping": DomainEvent, "Note": object}
    source = """
def unguarded(env):
    env.emit(Ping())

def observer_event(env):
    env.emit(Note())

def direct(self):
    if self.env.wants(Ping):
        self.env.emit(Ping())

def bound(env):
    wants = env.wants(Ping)
    for _ in range(2):
        if wants:
            env.emit(Ping())

def conditional(env):
    before = 1 if env.wants(Ping) else None
    if before is not None:
        env.emit(Ping())

def early_return(self):
    if not self._env.wants(Ping):
        return
    self._env.emit(Ping())

def wrong_branch(env):
    if env.wants(Ping):
        pass
    else:
        env.emit(Ping())
"""
    assert unguarded_emits(source, namespace) == [3, 32]
