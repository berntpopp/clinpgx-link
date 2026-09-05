#!/usr/bin/env python3
"""Enforce the repository's per-Python-module line budget."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

MAX_NONCOMMENT_LINES = 599
DEFAULT_TARGETS = (Path("clinpgx_link"),)
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
    }
)


def _python_files(targets: Sequence[Path]) -> Iterator[Path]:
    """Yield explicit Python targets and recursively discovered source files."""
    seen: set[Path] = set()
    for target in targets:
        if not target.exists():
            raise ValueError(f"target does not exist: {target}")
        if target.is_file():
            candidates = (target,) if target.suffix == ".py" else ()
        elif target.is_dir():
            if target.name in SKIP_DIRECTORIES:
                continue
            candidates = _python_files_in_directory(target)
        else:
            raise ValueError(f"target is not a regular file or directory: {target}")

        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                yield candidate


def _python_files_in_directory(root: Path) -> Iterator[Path]:
    """Yield Python files below ``root`` without descending into runtime trees."""
    for current_root, directories, names in os.walk(root, topdown=True):
        directories[:] = sorted(
            directory for directory in directories if directory not in SKIP_DIRECTORIES
        )
        for name in sorted(names):
            if name.endswith(".py"):
                yield Path(current_root) / name


def _noncomment_lines(path: Path) -> int:
    """Count nonblank, non-comment physical lines in one Python file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"could not read {path}: {error}") from error
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets",
        nargs="*",
        type=Path,
        help="Python files or source directories (default: clinpgx_link)",
    )
    args = parser.parse_args(argv)
    targets = args.targets or DEFAULT_TARGETS

    try:
        violations = [
            (path, count)
            for path in _python_files(targets)
            if (count := _noncomment_lines(path)) > MAX_NONCOMMENT_LINES
        ]
    except ValueError as error:
        sys.stderr.write(f"check-file-size: {error}\n")
        return 2

    for path, count in violations:
        sys.stderr.write(
            f"check-file-size: {path} has {count} nonblank/noncomment lines "
            f"(maximum {MAX_NONCOMMENT_LINES})\n"
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
