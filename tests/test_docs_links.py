from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts" / "check_docs_links.py"


def _checker_module():
    spec = importlib.util.spec_from_file_location("check_docs_links", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_accepts_spaced_meta_refresh(tmp_path, monkeypatch, capsys) -> None:
    checker = _checker_module()
    (tmp_path / "next").mkdir()
    (tmp_path / "next" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<meta http-equiv="refresh" content="0; url=next/">', encoding="utf-8"
    )
    (tmp_path / "missing.html").write_text(
        '<meta http-equiv="refresh" content="0; url=missing-target/">',
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "SITE", tmp_path)

    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "BROKEN INTERNAL LINKS" in output
