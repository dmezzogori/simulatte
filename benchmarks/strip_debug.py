"""Write a copy of an installed ``simulatte==0.12.0`` without its ``env.debug(...)`` calls (G3 report §5.2).

0.12.0 builds an f-string and keyword arguments for ``env.debug`` on every queue entry, release, processing start,
pre-shop-pool entry and exit and shop-floor step, even at the default INFO level. The branch replaced these calls
with events guarded by ``env.wants``. Comparing the branch with this stripped copy isolates what SP1 added on the
unobserved path. The CI gate uses this copy as its second baseline, next to the released 0.12.0 (decision D55).

Every expression statement ``<x>.env.debug(...)`` in ``psp.py``, ``server.py``, ``shopfloor.py`` and ``router.py``
is replaced by ``pass`` (12 statements in 0.12.0); the rest of the package is copied unchanged.

Usage::

    python benchmarks/strip_debug.py /tmp/base/lib/python3.14/site-packages/simulatte /tmp/stripped
    PYTHONPATH=/tmp/stripped /tmp/base/bin/python benchmarks/run.py --label "0.12.0 without env.debug calls" ...

The first argument is the ``simulatte`` package directory (installed or a source tree); the copy is written to
``DEST/simulatte``, which must not exist. ``PYTHONPATH`` puts it before the installed package.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path
from typing import TypeGuard

MODULES = ("psp.py", "server.py", "shopfloor.py", "router.py")


def _is_env_debug(node: ast.AST) -> TypeGuard[ast.Expr]:
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "debug"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "env"
    )


def strip(source: str) -> tuple[str, int]:
    """`source` with every ``<x>.env.debug(...)`` statement replaced by ``pass``, and the number replaced."""
    spans = [(n.lineno, n.end_lineno or n.lineno) for n in ast.walk(ast.parse(source)) if _is_env_debug(n)]
    lines = source.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        first = lines[start - 1]
        lines[start - 1 : end] = [first[: len(first) - len(first.lstrip())] + "pass\n"]
    return "".join(lines), len(spans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="the simulatte package directory to copy")
    parser.add_argument("dest", type=Path, help="directory that receives the stripped simulatte/")
    args = parser.parse_args(argv)
    target = args.dest / "simulatte"
    if not (args.package / "__init__.py").is_file():
        parser.error(f"{args.package} is not a package directory")
    if target.exists():
        parser.error(f"{target} already exists")
    shutil.copytree(args.package, target, ignore=shutil.ignore_patterns("__pycache__"))
    total = 0
    for name in MODULES:
        path = target / name
        text, count = strip(path.read_text())
        path.write_text(text)
        print(f"{name}: {count} env.debug statements removed", file=sys.stderr)
        total += count
    print(f"{total} statements removed; copy in {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
