# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
from pathlib import Path

import pytest

from .checker import BcdTo7SegChecker
from .model import bcd_to_7seg
from .stimulus import display_sequence

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("digit,expected", enumerate(
    [0x40, 0x79, 0x24, 0x30, 0x19, 0x12, 0x02, 0x78, 0x00, 0x10]))
def test_active_low_decimal_patterns(digit: int, expected: int) -> None:
    assert bcd_to_7seg(digit) == expected
    assert bcd_to_7seg(digit, 0) == expected ^ 0x7F


@pytest.mark.parametrize("digit", range(10, 16))
def test_invalid_bcd_blanks_instead_of_hex(digit: int) -> None:
    assert bcd_to_7seg(digit, 1) == 0x7F
    assert bcd_to_7seg(digit, 0) == 0


@pytest.mark.parametrize("digit,polarity", [(-1, 0), (16, 1), (0, 2)])
def test_input_validation(digit: int, polarity: int) -> None:
    with pytest.raises(ValueError):
        bcd_to_7seg(digit, polarity)


def test_seed_and_exhaustive_coverage() -> None:
    sequence = display_sequence(7)
    assert sequence == display_sequence(7)
    assert sequence != display_sequence(8)
    assert set(sequence) == {(d, p) for d in range(16) for p in (0, 1)}
    checker = BcdTo7SegChecker()
    for digit, polarity in sequence:
        expected = bcd_to_7seg(digit, polarity)
        checker.check(expected, expected)
        with pytest.raises(AssertionError):
            checker.check(expected, expected ^ 0x7F)
    assert checker.samples == len(sequence)


@pytest.mark.simulation
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
def test_bcd_to_7seg_simulation(sim: str, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=bcd_to_7seg", f"SIM={sim}",
         f"SIM_BUILD={tmp_path}/build", f"RESULTS_DIR={tmp_path}/results"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
