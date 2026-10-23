# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import EdgeDetectorChecker
from .model import EdgeDetectorModel
from .stimulus import edge_sequence

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("rising,expected", [
    (1, [1, 0, 0, 0, 1, 0]),
    (0, [0, 0, 1, 0, 0, 1]),
])
def test_edges_are_one_cycle(rising: int, expected: list[int]) -> None:
    model = EdgeDetectorModel(rising)
    assert [model.tick(x) for x in [1, 1, 0, 0, 1, 0]] == expected


@pytest.mark.parametrize("rising", [0, 1])
def test_reset_high_input_contract(rising: int) -> None:
    model = EdgeDetectorModel(rising)
    model.tick(1)
    assert model.tick(1, rst_n=False) == 0
    assert model.tick(1) == rising
    assert model.tick(1) == 0


def test_configuration_and_seed() -> None:
    with pytest.raises(ValueError):
        EdgeDetectorModel(2)
    with pytest.raises(ValueError):
        edge_sequence(0, 1)
    assert edge_sequence(100, 7) == edge_sequence(100, 7)
    assert edge_sequence(100, 7) != edge_sequence(100, 8)


@pytest.mark.parametrize("rising", [0, 1])
@pytest.mark.parametrize("fault", ["wrong-polarity", "stuck-low", "level-not-edge"])
def test_checker_detects_faults(rising: int, fault: str) -> None:
    model, opposite = EdgeDetectorModel(rising), EdgeDetectorModel(1 - rising)
    checker = EdgeDetectorChecker()
    for level in edge_sequence(100, 7):
        expected = model.tick(level)
        wrong = opposite.tick(level)
        actual = {"wrong-polarity": wrong, "stuck-low": 0,
                  "level-not-edge": int(level == rising)}[fault]
        checker.check(expected, actual)
    assert not checker.passed
    assert "pulse" in checker.report()


def test_checker_accepts_matching_trace() -> None:
    checker = EdgeDetectorChecker()
    for value in [0, 1, 0, 0]:
        checker.check(value, value)
    assert checker.passed


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
@pytest.mark.parametrize("rising", [0, 1])
def test_edge_detector_simulation(sim: str, rising: int, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=edge_detector", f"SIM={sim}",
         f"RISING={rising}", f"SIM_BUILD={tmp_path.as_posix()}/build",
         f"RESULTS_DIR={tmp_path.as_posix()}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
