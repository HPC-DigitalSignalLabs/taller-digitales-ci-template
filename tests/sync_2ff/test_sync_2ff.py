# SPDX-License-Identifier: Apache-2.0
"""Unit tests plus both supported open simulators."""
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import Sync2ffChecker
from .model import Sync2ffModel
from .stimulus import level_sequence

ROOT = Path(__file__).resolve().parents[2]


def test_two_samples_not_one() -> None:
    model = Sync2ffModel()
    assert [model.tick(x) for x in [1, 1, 0, 0]] == [0, 1, 1, 0]


def test_reset_flushes_both_stages() -> None:
    model = Sync2ffModel()
    model.tick(1)
    model.tick(1)
    assert model.tick(1, rst_n=False) == 0
    assert model.tick(0) == 0
    assert model.tick(0) == 0


def test_seed_is_replayable_and_invalid_length_rejected() -> None:
    assert level_sequence(100, 7) == level_sequence(100, 7)
    assert level_sequence(100, 7) != level_sequence(100, 8)
    with pytest.raises(ValueError):
        level_sequence(0, 7)


@pytest.mark.parametrize("fault", ["bypass", "one-stage", "stuck-low", "inverted"])
def test_checker_detects_digital_faults(fault: str) -> None:
    model = Sync2ffModel()
    checker = Sync2ffChecker()
    for level in level_sequence(100, 7):
        expected = model.tick(level)
        actual = {"bypass": level, "one-stage": model.stage1,
                  "stuck-low": 0, "inverted": 1 - expected}[fault]
        checker.check(expected, actual)
    assert not checker.passed
    assert "cycle" in checker.report()


def test_checker_accepts_matching_trace() -> None:
    checker = Sync2ffChecker()
    for expected in [0, 1, 1, 0]:
        checker.check(expected, expected)
    assert checker.passed


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
def test_sync_2ff_simulation(sim: str, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=sync_2ff", f"SIM={sim}",
         f"SIM_BUILD={tmp_path.as_posix()}/build",
         f"RESULTS_DIR={tmp_path.as_posix()}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
