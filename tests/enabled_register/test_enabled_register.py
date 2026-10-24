# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import EnabledRegisterChecker
from .model import EnabledRegisterModel
from .stimulus import register_sequence

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", [1, 8, 32, 64])
def test_load_hold_reset(width: int) -> None:
    model = EnabledRegisterModel(width)
    maximum = (1 << width) - 1
    assert model.tick(maximum, 0) == 0
    assert model.tick(maximum, 1) == maximum
    assert model.tick(0, 0) == maximum
    assert model.tick(maximum, 1, rst_n=False) == 0
    assert model.tick(maximum, 0) == 0
    assert model.tick(0, 1) == 0


def test_validation_and_seed() -> None:
    with pytest.raises(ValueError):
        EnabledRegisterModel(0)
    for value in [-1, 256]:
        with pytest.raises(ValueError):
            EnabledRegisterModel(8).tick(value, 1)
    with pytest.raises(ValueError):
        register_sequence(0, 10, 7)
    with pytest.raises(ValueError):
        register_sequence(8, 0, 7)
    assert register_sequence(8, 100, 7) == register_sequence(8, 100, 7)
    assert register_sequence(8, 100, 7) != register_sequence(8, 100, 8)


@pytest.mark.parametrize("expected,actual", [(0, 1), (255, 0), (0, 255)])
def test_checker_rejects_wrong_storage(expected: int, actual: int) -> None:
    with pytest.raises(AssertionError):
        EnabledRegisterChecker().check(expected, actual)


def test_checker_accepts_matching_trace() -> None:
    checker = EnabledRegisterChecker()
    for value in [0, 255, 255, 0]:
        checker.check(value, value)
    assert checker.samples == 4


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
@pytest.mark.parametrize("width", [1, 8, 32, 64])
def test_enabled_register_simulation(sim: str, width: int, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=enabled_register", f"SIM={sim}", f"WIDTH={width}",
         f"SIM_BUILD={tmp_path}/build", f"RESULTS_DIR={tmp_path}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]


@pytest.mark.parametrize("width", [1, 8, 32])
def test_stimulus_exercises_reset_enable_conflict(width: int) -> None:
    maximum = (1 << width) - 1
    sequence = register_sequence(width, 50, 9)
    assert (maximum, 1, 0) in sequence
    assert all(0 <= data <= maximum and enable in (0, 1) and rst_n in (0, 1)
               for data, enable, rst_n in sequence)
