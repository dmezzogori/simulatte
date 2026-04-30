from __future__ import annotations

import ast
from pathlib import Path


INTRALOGISTICS_DIR = Path("src/simulatte/intralogistics")
FORBIDDEN_IMPORTS = {
    "simulatte.server",
    "simulatte.shopfloor",
    "simulatte.job",
    "simulatte.typing",
    "simulatte.policies",
    "simulatte.psp",
    "simulatte.router",
    "simulatte.builders",
}


class TestImportAudit:
    def test_no_forbidden_imports(self) -> None:
        violations = []
        for py_file in sorted(INTRALOGISTICS_DIR.glob("*.py")):
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in FORBIDDEN_IMPORTS:
                        if node.module == forbidden or node.module.startswith(forbidden + "."):
                            violations.append(f"{py_file.name}: imports {node.module}")
        assert violations == [], "Forbidden imports found:\n" + "\n".join(violations)

    def test_future_annotations(self) -> None:
        missing = []
        for py_file in sorted(INTRALOGISTICS_DIR.glob("*.py")):
            content = py_file.read_text()
            if "from __future__ import annotations" not in content:
                missing.append(py_file.name)
        assert missing == [], "Missing future annotations:\n" + "\n".join(missing)
