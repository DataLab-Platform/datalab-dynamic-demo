"""Dependency rules for host-independent plugin layers."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "datalab_dynamic_demo"
PACKAGE_NAME = "datalab_dynamic_demo"
HOST_MODULES = (
    "PyQt5",
    "PyQt6",
    "PySide6",
    "datalab.gui",
    f"{PACKAGE_NAME}.adapters",
    "js",
    "pyodide",
    "qtpy",
)
FORBIDDEN_LAYER_IMPORTS = {
    "core": ("datalab", f"{PACKAGE_NAME}.workflow"),
    "workflow": (),
}


def _module_imports(filename: Path) -> set[str]:
    """Return direct imports declared by one Python source file."""
    tree = ast.parse(filename.read_text(encoding="utf-8"), filename=str(filename))
    relative_parts = filename.relative_to(PACKAGE_ROOT).with_suffix("").parts
    package_parts = (PACKAGE_NAME, *relative_parts[:-1])
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module is not None:
                    imports.add(node.module)
                continue
            base_parts = package_parts[: len(package_parts) - node.level + 1]
            if node.module is not None:
                imports.add(".".join((*base_parts, *node.module.split("."))))
            else:
                imports.update(
                    ".".join((*base_parts, *alias.name.split(".")))
                    for alias in node.names
                )
    return imports


def test_package_root_does_not_import_host_adapters() -> None:
    """Importing package identity must not load a host adapter."""
    imports = _module_imports(PACKAGE_ROOT / "__init__.py")
    assert not any(name.startswith(f"{PACKAGE_NAME}.adapters") for name in imports)


def test_import_scanner_resolves_relative_imports() -> None:
    """Relative imports cannot bypass the architecture checks."""
    imports = _module_imports(PACKAGE_ROOT / "workflow" / "__init__.py")
    assert f"{PACKAGE_NAME}.workflow.recipes" in imports


def test_headless_layers_do_not_import_host_modules() -> None:
    """Core and workflow remain independent from Desktop and Web hosts."""
    violations: list[str] = []
    for layer in ("core", "workflow"):
        forbidden = HOST_MODULES + FORBIDDEN_LAYER_IMPORTS[layer]
        for filename in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
            for imported_module in sorted(_module_imports(filename)):
                if any(
                    imported_module == prefix
                    or imported_module.startswith(f"{prefix}.")
                    for prefix in forbidden
                ):
                    violations.append(
                        f"{filename.relative_to(PACKAGE_ROOT)} imports "
                        f"{imported_module}"
                    )
    assert not violations, "\n".join(violations)
