from __future__ import annotations

from tabulate import tabulate


def render_table(title: str, headers, rows) -> None:
    print(f"## {title}")
    print(tabulate(rows, headers=headers, tablefmt="fancy_grid"))
