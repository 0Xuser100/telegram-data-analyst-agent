"""The layer rule, enforced.

HIGH_LEVEL_DESIGN.md claims dependencies point downward only. In the old flat
layout that was a promise in prose; nothing checked it, and it had already been
broken once (the runner reached up into the approval cards). Now the folders
carry the claim and this test keeps it honest.
"""

import ast
import os
from pathlib import Path

import pytest

# Higher rank may import lower rank. Never the other way.
RANK = {"entrypoints": 3, "conversation": 2, "agent": 1, "plumbing": 0}

# Anchored on this file, not the working directory: the sandbox fixture chdirs
# into a temp directory, so a relative glob would find nothing.
SRC = Path(__file__).resolve().parent.parent / "src"
SOURCES = sorted(SRC.rglob("analyst/**/*.py"))


def _layer(module: str) -> str | None:
    """The layer a dotted module path belongs to, if any.

    `analyst.config` is shared and deliberately layerless: it has no internal
    imports, so anything may read it.
    """
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in RANK else None


def _module_of(path: Path) -> str:
    return str(path.relative_to(SRC).with_suffix("")).replace(os.sep, ".")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
    return [m for m in found if m.startswith("analyst")]


def test_the_layers_are_all_populated():
    """A typo in a path would otherwise make this whole file vacuously pass."""
    assert SOURCES, f"no sources found under {SRC}"
    seen = {_layer(_module_of(f)) for f in SOURCES}
    assert set(RANK) <= seen


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_nothing_imports_a_higher_layer(path):
    module = _module_of(path)
    source_layer = _layer(module)
    if source_layer is None:          # analyst.config, package __init__ files
        return
    for imported in _imported_modules(path):
        target_layer = _layer(imported)
        if target_layer is None:
            continue
        assert RANK[target_layer] <= RANK[source_layer], (
            f"{module} ({source_layer}) imports {imported} ({target_layer}); "
            "dependencies must point downward only"
        )
