# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import ControlFsmChecker
from .model import ControlFsmModel
from .stimulus import control_sequence

ROOT = Path(__file__).resolve().parents[2]


def test_directed_protocol() -> None:
    model = ControlFsmModel()
    inputs = [(0, 1), (1, 1), (1, 0), (0, 1), (1, 1), (0, 1), (1, 0)]
    assert [model.tick(*pair) for pair in inputs] == [
        (0, 0), (1, 0), (1, 0), (0, 1), (0, 0), (0, 0), (1, 0)]


def test_held_start_and_finish_turnaround() -> None:
    model = ControlFsmModel()
    assert [model.tick(1, 1) for _ in range(6)] == [
        (1, 0), (0, 1), (0, 0), (1, 0), (0, 1), (0, 0)]


@pytest.mark.parametrize("complete", [False, True])
def test_reset_aborts_running_and_done(complete: bool) -> None:
    model = ControlFsmModel()
    model.tick(1, 0)
    if complete:
        model.tick(0, 1)
    assert model.tick(1, 1, rst_n=False) == (0, 0)
    assert model.tick(0, 1) == (0, 0)


@pytest.mark.parametrize("actual", [(0, 0), (0, 1), (1, 1)])
def test_checker_detects_divergence(actual: tuple[int, int]) -> None:
    with pytest.raises(AssertionError):
        ControlFsmChecker().check((1, 0), actual)


def test_seed_and_matching_checker() -> None:
    assert control_sequence(100, 7) == control_sequence(100, 7)
    assert control_sequence(100, 7) != control_sequence(100, 8)
    with pytest.raises(ValueError):
        control_sequence(0, 7)
    checker = ControlFsmChecker()
    for value in [(0, 0), (1, 0), (0, 1)]:
        checker.check(value, value)
    assert checker.samples == 3


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
def test_control_fsm_simulation(sim: str, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=control_fsm", f"SIM={sim}",
         f"SIM_BUILD={tmp_path}/build", f"RESULTS_DIR={tmp_path}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
