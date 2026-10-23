# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import Bin2BcdChecker
from .model import bin2bcd, digit_count
from .stimulus import binary_sequence

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", range(1, 13))
def test_exhaustive_small_values(width: int) -> None:
    for value in range(1 << width):
        # Decimal string is an additional oracle, independent of packed arithmetic.
        assert bin2bcd(value, width) == int(str(value), 16)


@pytest.mark.parametrize("width", range(1, 65))
def test_capacity_and_maximum(width: int) -> None:
    maximum = (1 << width) - 1
    assert digit_count(width) >= len(str(maximum))
    assert bin2bcd(maximum, width) == int(str(maximum), 16)
    assert bin2bcd(0, width) == 0


@pytest.mark.parametrize("width", [0, -1, 65])
def test_invalid_width(width: int) -> None:
    with pytest.raises(ValueError):
        bin2bcd(0, width)


@pytest.mark.parametrize("value", [-1, 256])
def test_input_overflow_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        bin2bcd(value, 8)


def test_seed_boundaries_and_checker() -> None:
    values = binary_sequence(32, 100, 7)
    assert values == binary_sequence(32, 100, 7)
    assert values != binary_sequence(32, 100, 8)
    for value in [9, 10, 11, 99, 100, 101, 65535, 65536, (1 << 32) - 1]:
        assert value in values
    checker = Bin2BcdChecker(digit_count(32))
    for value in values:
        expected = bin2bcd(value, 32)
        checker.check(expected, expected)
    assert checker.samples == len(values)
    with pytest.raises(ValueError):
        binary_sequence(8, 0, 7)


@pytest.mark.parametrize("actual", [0x55, 0x25A, 0x1000, -1])
def test_checker_catches_truncation_invalid_digit_and_overflow(actual: int) -> None:
    with pytest.raises(AssertionError):
        Bin2BcdChecker(3).check(0x255, actual)


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
@pytest.mark.parametrize("width", [1, 4, 8, 16, 32, 64])
def test_bin2bcd_simulation(sim: str, width: int, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=bin2bcd", f"SIM={sim}", f"WIDTH={width}",
         f"SIM_BUILD={tmp_path}/build", f"RESULTS_DIR={tmp_path}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
