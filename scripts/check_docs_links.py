# scripts/check_docs_links.py
"""Fail if any internal link in the built site/ does not resolve to a file.

zensical has no --strict mode, so this is our link gate. Run AFTER `zensical build`.
Covers page-to-page links; skips external URLs, anchors, mailto, and assets.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SITE = Path("site")
HREF = re.compile(r'href="([^"]+)"')
SKIP_PREFIXES = ("http://", "https://", "mailto:", "//", "data:", "#", "javascript:")


def resolve(base: Path, target: str) -> bool:
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return True
    if target.startswith("/"):
        p = SITE / target.lstrip("/")
    else:
        p = base / target
    candidates = [p, p / "index.html"]
    if p.suffix == "":
        candidates.append(Path(f"{p}.html"))
    return any(c.exists() for c in candidates)


def main() -> int:
    if not SITE.exists():
        print("site/ not found — run `uv run zensical build` first", file=sys.stderr)
        return 2
    missing: list[tuple[str, str]] = []
    for html in SITE.rglob("*.html"):
        for m in HREF.finditer(html.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(SKIP_PREFIXES):
                continue
            if "/assets/" in target:
                continue
            if not resolve(html.parent, target):
                missing.append((str(html.relative_to(SITE)), target))
    if missing:
        print(f"BROKEN INTERNAL LINKS: {len(missing)}")
        for src, tgt in sorted(set(missing))[:200]:
            print(f"  {src} -> {tgt}")
        return 1
    print(f"OK — all internal links resolve ({sum(1 for _ in SITE.rglob('*.html'))} pages scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
