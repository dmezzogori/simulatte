from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "examples"
EXAMPLES = ROOT / "examples"

# doc page -> example script that must be embedded verbatim in a { .run } block
PAIRS = {
    "dispatching-stateless.md": "gallery_dispatching_stateless.py",
    "dispatching-parameterized.md": "gallery_dispatching_parameterized.py",
    "dispatching-focus.md": "gallery_dispatching_focus.py",
    "release-workload.md": "gallery_release_workload.py",
    "release-wip.md": "gallery_release_wip.py",
    "release-triggers.md": "gallery_release_triggers.py",
}

RUN_BLOCK = re.compile(r"```python \{ \.run \}\n(.*?)\n```", re.DOTALL)


def test_run_blocks_match_example_files() -> None:
    for doc_name, script_name in PAIRS.items():
        doc = (DOCS / doc_name).read_text()
        script = (EXAMPLES / script_name).read_text()
        blocks = RUN_BLOCK.findall(doc)
        assert blocks, f"{doc_name}: no `python {{ .run }}` block found"
        assert len(blocks) == 1, f"{doc_name}: expected exactly one `python {{ .run }}` block, found {len(blocks)}"
        # The embedded block must equal the script file (trailing newline tolerant).
        assert blocks[0].strip("\n") == script.strip("\n"), f"{doc_name} embedded code has drifted from {script_name}"


def test_pairs_cover_all_gallery_scripts() -> None:
    scripts = sorted(p.name for p in EXAMPLES.glob("gallery_*.py"))
    assert sorted(PAIRS.values()) == scripts
