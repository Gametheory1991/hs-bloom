#!/usr/bin/env python3
"""Fail if any Python file under collector/ imports a module that cannot
resolve to the standard library, a declared dependency, or the repo tree.

Background: in 2026-10-06, collector/src/collector/fetchers/finra_factbook.py
contained ``from collector.fetchers.sec_ncen import quarter_end`` where
sec_ncen.py existed only in a developer's working tree -- never committed.
Every local test passed; every Render deploy crashed at import time with
ModuleNotFoundError, three times in a row. This script makes that class of
bug fail CI instead of production.

What counts as resolvable:
  * stdlib modules (sys.stdlib_module_names),
  * third-party distributions declared in collector/pyproject.toml
    (main dependencies + the dev extra; the postgres extra is intentionally
    excluded -- the Docker image does not install it, so an unguarded
    psycopg import would crash prod too),
  * dotted paths that exist under collector/src (the installed ``collector``
    package) or collector/tests (the ``tests`` package),
  * imports guarded by ``try/except ImportError`` (explicitly optional).

Usage:  python scripts/check_imports.py   (run from the repo root)
Exit 0 when every import resolves; exit 1 listing each failure as
path:line: <import statement> -> <reason>.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_DIR = REPO_ROOT / "collector"
SRC_DIR = COLLECTOR_DIR / "src"
TESTS_DIR = COLLECTOR_DIR / "tests"
PYPROJECT = COLLECTOR_DIR / "pyproject.toml"

# Distribution names whose import name differs from the normalized dist name.
DIST_TO_IMPORT = {
    "pyyaml": "yaml",
    "pytest-asyncio": "pytest_asyncio",
}

# An import inside `try: ... except <one of these>:` is explicitly optional
# (e.g. an uninstalled extra) and is not checked.
_GUARDING_EXCEPTIONS = {"ImportError", "Exception", "BaseException"}


# Matches the distribution name at the start of a PEP 508 requirement string,
# stopping before extras ([...]), version specifiers, or environment markers.
_DIST_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def dist_to_import(dist: str) -> str:
    dist = dist.split(";")[0]  # strip environment markers
    m = _DIST_NAME_RE.match(dist.strip())
    name = m.group(0) if m else dist.strip()
    key = name.lower()
    return DIST_TO_IMPORT.get(key, key.replace("-", "_"))


def declared_third_party() -> set[str]:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    names = {dist_to_import(d) for d in data["project"]["dependencies"]}
    dev = data["project"].get("optional-dependencies", {}).get("dev", [])
    names.update(dist_to_import(d) for d in dev)
    # NOTE: the "postgres" extra is intentionally excluded. The Docker image
    # installs only the main dependencies, so an unguarded `import psycopg`
    # would crash production and must fail this check. Guard it with
    # try/except ImportError if it is truly optional.
    return names


def repo_modules() -> set[str]:
    """Every dotted module path provided by the repo itself.

    collector/src  -> collector, collector.fetchers, ...
    collector/tests -> tests, tests.test_foo, tests.fixtures, ...
    Parent prefixes are included so namespace-style packages resolve.
    """
    mods: set[str] = set()

    def add_with_parents(dotted: str) -> None:
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            mods.add(".".join(parts[:i]))

    for root, prefix in ((SRC_DIR, ""), (TESTS_DIR, "tests")):
        if not root.is_dir():
            continue
        for py in sorted(root.rglob("*.py")):
            rel = py.relative_to(root).with_suffix("")
            parts = list(rel.parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            if not parts:
                continue
            dotted = f"{prefix}.{'.'.join(parts)}" if prefix else ".".join(parts)
            add_with_parents(dotted)
    return mods


def _handler_guards(node: ast.Try) -> bool:
    for h in node.handlers:
        t = h.type
        if t is None:  # bare `except:`
            return True
        if isinstance(t, ast.Name) and t.id in _GUARDING_EXCEPTIONS:
            return True
        if isinstance(t, ast.Tuple) and any(
            isinstance(e, ast.Name) and e.id in _GUARDING_EXCEPTIONS
            for e in t.elts
        ):
            return True
    return False


class ImportVisitor(ast.NodeVisitor):
    """Collect (lineno, kind, dotted, level, names, text, guarded) records."""

    def __init__(self, source: str) -> None:
        # (lineno, dotted, level, names, stmt_text, guarded)
        self.found: list[tuple] = []
        self._guarded = 0
        self._source = source

    def visit_Try(self, node: ast.Try) -> None:
        if _handler_guards(node):
            self._guarded += 1
            self.generic_visit(node)
            self._guarded -= 1
        else:
            self.generic_visit(node)

    # except* (3.11+); assigning the name is safe on older Pythons.
    visit_TryStar = visit_Try

    def _record(self, node: ast.AST, dotted: str | None, level: int,
                names: list[str]) -> None:
        try:
            text = ast.get_source_segment(self._source, node) or ""
        except Exception:  # noqa: BLE001 -- best effort only
            text = ""
        self.found.append(
            (node.lineno, dotted, level, names,
             " ".join(text.split()), self._guarded > 0)
        )

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self._record(node, a.name, 0, [])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [a.name for a in node.names]
        self._record(node, node.module, node.level or 0, names)


def package_parts(path: Path) -> list[str]:
    """Dotted package containing `path`, for relative-import resolution."""
    if path.is_relative_to(SRC_DIR):
        rel = path.relative_to(SRC_DIR).with_suffix("").parts
    else:
        rel = ("tests",) + path.relative_to(TESTS_DIR).with_suffix("").parts
    parts = list(rel)
    if parts[-1] != "__init__":
        parts = parts[:-1]  # containing package of a plain module
    return parts


def check_record(rec: tuple, pkg_parts: list[str], stdlib: set[str],
                 thirdparty: set[str], repo_mods: set[str]) -> str | None:
    """Return an error string, or None if the import resolves."""
    lineno, dotted, level, names, text, guarded = rec
    if guarded:
        return None  # explicitly optional dependency
    if level:
        # Relative import: level=1 -> current package, 2 -> parent, ...
        if len(pkg_parts) < level:
            return f"{text} -> relative import goes beyond top-level package"
        base = pkg_parts[: len(pkg_parts) - level + 1]
        targets = []
        if dotted:
            targets.append(".".join([*base, dotted]))
        else:
            targets.extend(
                ".".join([*base, n]) for n in names if n != "*"
            )
        for t in targets:
            if t not in repo_mods:
                return f"{text} -> resolves to {t!r}, not in repo tree"
        return None
    top = (dotted or "").split(".")[0]
    if not top:
        return f"{text} -> empty import"
    if top in stdlib or top in thirdparty:
        return None
    if dotted in repo_mods:
        return None
    if top in ("collector", "tests"):
        return f"{text} -> module {dotted!r} not found in repo tree"
    return (
        f"{text} -> unknown top-level package {top!r} "
        "(not stdlib, not a declared dependency, not in repo)"
    )


def main() -> int:
    if not PYPROJECT.is_file():
        print(f"error: {PYPROJECT} not found; run from the repo root",
              file=sys.stderr)
        return 2
    stdlib = set(sys.stdlib_module_names)
    thirdparty = declared_third_party()
    repo_mods = repo_modules()

    failures: list[str] = []
    files = sorted(COLLECTOR_DIR.rglob("*.py"))
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}:1: "
                            f"unparsable ({exc})")
            continue
        visitor = ImportVisitor(source)
        visitor.visit(tree)
        try:
            pkg_parts = package_parts(path)
        except ValueError:
            # .py file directly under collector/ (not src/ or tests/):
            # treat as its own top-level context; absolute imports only.
            pkg_parts = []
        rel = path.relative_to(REPO_ROOT)
        for rec in visitor.found:
            err = check_record(rec, pkg_parts, stdlib, thirdparty, repo_mods)
            if err:
                failures.append(f"{rel}:{rec[0]}: {err}")

    if failures:
        print(f"{len(failures)} unresolvable import(s):")
        for f in failures:
            print(" ", f)
        return 1
    print(f"OK: {len(files)} files, all imports resolve "
          f"({len(thirdparty)} declared third-party packages checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
