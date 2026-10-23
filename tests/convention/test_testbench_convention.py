# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Enforce the testbench convention (ADR-0002) mechanically.

ADR-0002 rejects PyUVM and substitutes a fixed four-file testbench convention. That
substitution only pays for itself if the convention is actually held, so it is checked
here instead of by code review.

The load-bearing rule is the third one: ``checker.py`` must import neither ``cocotb``
nor any hardware driver. That is what lets the identical checker judge a simulation
trace and a trace captured from the FPGA over JTAG, which *is* the stimulus/checker
reuse metric of OE3. If this test fails, the reuse metric is no longer measurable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]

# Modules that must stay pure Python, and what they may not reach for.
FORBIDDEN = {
    "model.py": ("cocotb", "serial", "intel_jtag_uart"),
    "stimulus.py": ("cocotb", "serial", "intel_jtag_uart"),
    "checker.py": ("cocotb", "serial", "intel_jtag_uart"),
}

# A testbench directory is any directory holding a CocoTB testbench.
TESTBENCH_DIRS = sorted(p.parent for p in TESTS_ROOT.glob("*/tb_*.py"))


def _imported_roots(path: Path) -> set[str]:
    """Return the top-level package name of every import in ``path``.

    Uses the AST rather than a text search so that the word appearing in a comment,
    a docstring or a string literal does not produce a false failure.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module root outside the package.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_at_least_one_testbench_exists() -> None:
    """Guard against this whole file silently passing on an empty glob."""
    assert TESTBENCH_DIRS, (
        "no tb_*.py found under tests/; either the convention was renamed or this "
        "test is scanning the wrong directory"
    )


@pytest.mark.parametrize("tb_dir", TESTBENCH_DIRS, ids=lambda p: p.name)
def test_convention_files_present(tb_dir: Path) -> None:
    """Every testbench directory carries the files of the convention."""
    missing = [
        name
        for name in ("model.py", "stimulus.py", "checker.py")
        if not (tb_dir / name).exists()
    ]
    assert not missing, (
        f"{tb_dir.name}/ does not follow the ADR-0002 convention; missing: {missing}"
    )


@pytest.mark.parametrize("tb_dir", TESTBENCH_DIRS, ids=lambda p: p.name)
def test_pure_modules_stay_pure(tb_dir: Path) -> None:
    """model/stimulus/checker must not import cocotb or any transport driver."""
    violations: list[str] = []
    for name, forbidden in FORBIDDEN.items():
        path = tb_dir / name
        if not path.exists():
            continue
        offending = _imported_roots(path) & set(forbidden)
        if offending:
            violations.append(f"{tb_dir.name}/{name} imports {sorted(offending)}")

    assert not violations, (
        "ADR-0002 violated — these modules are no longer transport-agnostic, so the "
        "same checker can no longer judge both simulation and FPGA traces, and the "
        "OE3 reuse metric is void:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("tb_dir", TESTBENCH_DIRS, ids=lambda p: p.name)
def test_cocotb_confined_to_the_driver(tb_dir: Path) -> None:
    """Only tb_*.py may import cocotb: it is the sole module that knows the DUT."""
    for path in sorted(tb_dir.glob("*.py")):
        if path.name.startswith(("tb_", "test_")):
            continue
        assert "cocotb" not in _imported_roots(path), (
            f"{tb_dir.name}/{path.name} imports cocotb; under ADR-0002 only "
            f"tb_*.py may do so"
        )


def test_no_cocotb_testbench_is_named_like_a_pytest_file() -> None:
    """PyTest would collect a CocoTB testbench named test_*.py and die on import.

    The failure mode is confusing (an ImportError for cocotb during collection, far
    from its cause), so it is worth catching by name.
    """
    suspects = [
        p for p in TESTS_ROOT.glob("*/test_*.py") if "cocotb" in _imported_roots(p)
    ]
    assert not suspects, (
        "these files import cocotb but are named test_*.py, so PyTest will collect "
        f"them outside the simulator and fail: {[str(p) for p in suspects]}"
    )
