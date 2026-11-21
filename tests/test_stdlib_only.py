"""The brief allows only the Python standard library inside system/."""

import ast
import sys
from pathlib import Path

SYSTEM = Path(__file__).resolve().parents[1] / "system"


def test_system_imports_only_stdlib_and_itself():
    bad = []
    for path in SYSTEM.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in {"screening", "__future__"}:
                    bad.append(f"{path.relative_to(SYSTEM)}: {name}")
    assert not bad, f"non-stdlib imports in system/: {bad}"
