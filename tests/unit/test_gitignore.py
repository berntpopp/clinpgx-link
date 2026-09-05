"""Regression tests for repository-local generated-file ignore rules."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git_ignores(path: str) -> bool:
    git = shutil.which("git")
    if git is None:
        raise AssertionError("git is required for ignore-rule regression tests")
    result = subprocess.run(  # noqa: S603
        [git, "check-ignore", "--no-index", "--quiet", "--", path],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    ("path",),
    [
        ("data/release.sqlite",),
        ("clinpgx_link/data/__pycache__/repository.cpython-312.pyc",),
        ("clinpgx_link/data/generated.cpython-312.pyc",),
        (".benchmarks/run.json",),
        (".worktrees/experiment/README.md",),
        (".superpowers/notes.md",),
    ],
)
def test_generated_and_scratch_paths_are_ignored(path: str) -> None:
    assert _git_ignores(path), f"expected Git to ignore {path}"


@pytest.mark.parametrize(
    ("path",),
    [
        ("clinpgx_link/data/schema.sql",),
        ("clinpgx_link/data/coverage.json",),
        ("clinpgx_link/data/repository.py",),
        ("tests/fixtures/exports/sourced/summary_annotations.tsv",),
    ],
)
def test_source_schema_coverage_and_test_fixtures_remain_eligible(path: str) -> None:
    assert not _git_ignores(path), f"expected Git to leave {path} eligible"
