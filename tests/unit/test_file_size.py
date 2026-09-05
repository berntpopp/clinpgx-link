"""Tests for the repository Python file-size gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "check_file_size.py"


def _run_checker(*targets: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *(str(target) for target in targets)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_python(path: Path, code_lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(["value = 1"] * code_lines) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(("code_lines", "expected_returncode"), [(599, 0), (600, 1)])
def test_checker_enforces_below_600_noncomment_lines(
    tmp_path: Path,
    code_lines: int,
    expected_returncode: int,
) -> None:
    source = tmp_path / "source" / "module.py"
    _write_python(source, code_lines)

    result = _run_checker(source.parent)

    assert result.returncode == expected_returncode
    if code_lines == 600:
        assert "600 nonblank/noncomment lines" in result.stderr
        assert str(source) in result.stderr


def test_checker_excludes_blank_and_comment_lines(tmp_path: Path) -> None:
    source = tmp_path / "source" / "comments.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(["# comment", "", "value = 1"] * 200 + ["# final comment", ""]),
        encoding="utf-8",
    )

    result = _run_checker(source.parent)

    assert result.returncode == 0


def test_checker_recurses_into_nested_python_sources(tmp_path: Path) -> None:
    source = tmp_path / "source" / "nested" / "deeper" / "module.py"
    _write_python(source, 600)

    result = _run_checker(source.parents[2])

    assert result.returncode == 1
    assert str(source) in result.stderr


def test_checker_does_not_scan_runtime_caches(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _write_python(source_root / "module.py", 1)
    for cache_name in ("__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"):
        _write_python(source_root / cache_name / "generated.py", 600)

    result = _run_checker(source_root)

    assert result.returncode == 0
    assert result.stderr == ""


def test_checker_rejects_missing_explicit_target(tmp_path: Path) -> None:
    missing = tmp_path / "missing-source"

    result = _run_checker(missing)

    assert result.returncode != 0
    assert "does not exist" in result.stderr
