# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Unit and CocoTB integration tests for the skid buffer."""

from __future__ import annotations

import shutil
import subprocess
import sys
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

TEST_DIR = Path(__file__).parent


def _load_local_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TEST_DIR / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker_module = _load_local_module("skid_buffer_checker", "checker.py")
model_module = _load_local_module("skid_buffer_model", "model.py")
stimulus_module = _load_local_module("skid_buffer_stimulus", "stimulus.py")

SkidBufferChecker = checker_module.SkidBufferChecker
SkidBufferModel = model_module.SkidBufferModel
SkidObservation = model_module.SkidObservation
SkidStimulus = stimulus_module.SkidStimulus
skid_sequence = stimulus_module.skid_sequence

TEMPLATE_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", [1, 8, 32])
def test_model_holds_payload_during_backpressure(width: int) -> None:
    model = SkidBufferModel(width)
    mask = (1 << width) - 1
    model.tick(SkidStimulus(valid=True, data=0xA5, ready=False))

    for _ in range(5):
        stimulus = SkidStimulus(valid=True, data=0, ready=False)
        observed = model.observe(stimulus)
        assert observed.m_valid
        assert observed.m_data == (0xA5 & mask)
        assert not observed.s_ready
        model.tick(stimulus)


@pytest.mark.parametrize("width", [1, 8, 32])
def test_model_accepts_one_transfer_per_cycle(width: int) -> None:
    model = SkidBufferModel(width)
    mask = (1 << width) - 1
    for value in range(20):
        stimulus = SkidStimulus(valid=True, data=value, ready=True)
        observed = model.observe(stimulus)
        assert observed.s_ready
        assert observed.m_valid
        assert observed.m_data == (value & mask)
        model.tick(stimulus)


def test_stimulus_is_seeded_and_contains_backpressure() -> None:
    first = skid_sequence(cycles=100, width=8, seed=17)
    second = skid_sequence(cycles=100, width=8, seed=17)
    other = skid_sequence(cycles=100, width=8, seed=18)
    assert first == second
    assert first != other
    assert any(item.valid and not item.ready for item in first)


@pytest.mark.parametrize(
    "arguments",
    [
        {"cycles": 0, "width": 8, "seed": 1},
        {"cycles": 10, "width": 0, "seed": 1},
    ],
)
def test_stimulus_rejects_invalid_arguments(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        skid_sequence(**arguments)


def test_checker_detects_an_injected_ready_fault() -> None:
    checker = SkidBufferChecker()
    checker.check(
        SkidObservation(s_ready=False, m_valid=True, m_data=7),
        SkidObservation(s_ready=True, m_valid=True, m_data=7),
    )
    assert not checker.passed
    assert checker.mismatches[0].field == "s_ready"


def _have_simulator(simulator: str) -> bool:
    executable = "iverilog" if simulator == "icarus" else simulator
    return shutil.which(executable) is not None


@pytest.mark.simulation
@pytest.mark.parametrize(
    ("width", "simulator"),
    [(1, "verilator"), (8, "verilator"), (32, "icarus")],
)
def test_skid_buffer_simulation_passes(width: int, simulator: str, tmp_path: Path) -> None:
    if not _have_simulator(simulator):
        pytest.skip(f"{simulator} is not installed")

    proc = subprocess.run(
        [
            "make",
            "sim",
            "DESIGN=skid_buffer",
            f"SIM={simulator}",
            f"WIDTH={width}",
            f"SIM_BUILD={tmp_path.as_posix()}/build",
            f"RESULTS_DIR={tmp_path.as_posix()}/results",
        ],
        cwd=TEMPLATE_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"{simulator} simulation failed for WIDTH={width} "
        f"(exit {proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n"
        f"--- stderr ---\n{proc.stderr[-4000:]}"
    )
