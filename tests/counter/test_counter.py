# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""PyTest layer over the CocoTB simulation, plus simulator-free unit tests.

Two things happen here, deliberately separated.

1. The model, stimulus and checker are tested as plain Python. These run
   everywhere, in milliseconds, with no Verilator. They are what guarantees the
   checker is genuinely decoupled from the simulator.
2. The CocoTB simulation is invoked through Make and skipped when Verilator is
   absent, so the same ``pytest tests/`` command works on a bare machine, in the
   DevContainer, on Kabré and on a hosted runner.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from checker import CounterChecker  # noqa: E402
from model import CounterModel  # noqa: E402
from stimulus import enable_sequence  # noqa: E402

TEMPLATE_ROOT = Path(__file__).resolve().parents[2]


# --- Simulator-free unit tests ------------------------------------------------


def test_model_holds_when_disabled() -> None:
    model = CounterModel(width=8)
    assert model.tick(enable=False) == 0
    assert model.tick(enable=True) == 1
    assert model.tick(enable=False) == 1


def test_model_wraps_at_width() -> None:
    model = CounterModel(width=4)
    for _ in range(16):
        model.tick(enable=True)
    assert model.count == 0


def test_model_reset_clears() -> None:
    model = CounterModel(width=8)
    model.tick(enable=True)
    model.reset()
    assert model.count == 0


@pytest.mark.parametrize("width", [0, -1])
def test_model_rejects_invalid_width(width: int) -> None:
    with pytest.raises(ValueError):
        CounterModel(width=width)


def test_stimulus_is_reproducible_for_a_given_seed() -> None:
    """Same seed, same sequence. Without this, a failure cannot be replayed."""
    a = list(enable_sequence(cycles=200, seed=7))
    b = list(enable_sequence(cycles=200, seed=7))
    c = list(enable_sequence(cycles=200, seed=8))
    assert a == b
    assert a != c


def test_stimulus_does_not_touch_global_rng() -> None:
    import random

    random.seed(99)
    expected = [random.random() for _ in range(5)]
    random.seed(99)
    list(enable_sequence(cycles=100, seed=1))
    assert [random.random() for _ in range(5)] == expected


@pytest.mark.parametrize("bad", [{"cycles": -1}, {"density": 1.5}, {"density": -0.1}])
def test_stimulus_rejects_invalid_arguments(bad: dict) -> None:
    with pytest.raises(ValueError):
        list(enable_sequence(cycles=bad.get("cycles", 10), seed=1, **{k: v for k, v in bad.items() if k != "cycles"}))


def test_checker_accepts_a_correct_stream() -> None:
    checker = CounterChecker(width=8)
    checker.reset()
    reference = CounterModel(width=8)
    for enable in enable_sequence(cycles=300, seed=42):
        checker.check(enable, reference.tick(enable))
    assert checker.passed
    assert checker.report() == ""


def test_checker_detects_an_injected_fault() -> None:
    """Mutation-style check: a wrong value must be caught and located."""
    checker = CounterChecker(width=8)
    checker.reset()
    reference = CounterModel(width=8)
    sequence = list(enable_sequence(cycles=100, seed=42))
    for index, enable in enumerate(sequence):
        value = reference.tick(enable)
        if index == 37:
            value = (value + 1) % 256  # inject a single-cycle divergence
        checker.check(enable, value)
    assert not checker.passed
    assert [m.cycle for m in checker.mismatches] == [37]
    assert "mismatch" in checker.report()


def test_checker_detects_a_stuck_at_zero_design() -> None:
    checker = CounterChecker(width=8)
    checker.reset()
    for enable in enable_sequence(cycles=50, seed=1):
        checker.check(enable, 0)
    assert not checker.passed


# --- The simulation itself ----------------------------------------------------


def _have_simulator() -> bool:
    return shutil.which("verilator") is not None


@pytest.mark.simulation
@pytest.mark.parametrize("width,sim", [(1, "verilator"), (4, "verilator"), (8, "verilator"), (4, "icarus")])
def test_counter_simulation_passes(width: int, sim: str, tmp_path: Path) -> None:
    """Run the CocoTB testbench through Make and require a clean result."""
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=counter", f"WIDTH={width}", f"SIM={sim}",
         f"SIM_BUILD={tmp_path.as_posix()}/build", f"RESULTS_DIR={tmp_path.as_posix()}/results"],
        cwd=TEMPLATE_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"simulation failed (exit {proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n"
        f"--- stderr ---\n{proc.stderr[-4000:]}"
    )
