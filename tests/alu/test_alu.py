# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Unit and CocoTB integration tests for the didactic ALU."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

TEST_DIR = Path(__file__).parent
TEMPLATE_ROOT = Path(__file__).resolve().parents[2]


def _load_local_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TEST_DIR / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker_module = _load_local_module("alu_checker", "checker.py")
model_module = _load_local_module("alu_model", "model.py")
stimulus_module = _load_local_module("alu_stimulus", "stimulus.py")

AluChecker = checker_module.AluChecker
AluObservation = model_module.AluObservation
AluModel = model_module.AluModel
AluStimulus = stimulus_module.AluStimulus
alu_sequence = stimulus_module.alu_sequence
OP_ADD = model_module.OP_ADD
OP_SUB = model_module.OP_SUB
OP_AND = model_module.OP_AND
OP_OR = model_module.OP_OR
OP_XOR = model_module.OP_XOR
OP_EQ = model_module.OP_EQ
OP_LT = model_module.OP_LT


@pytest.mark.parametrize("width", [1, 8, 32])
def test_model_covers_all_supported_operations(width: int) -> None:
    model = AluModel(width)
    mask = (1 << width) - 1

    assert model.evaluate(AluStimulus(mask, 1, OP_ADD)).result == 0
    assert model.evaluate(AluStimulus(mask, 1, OP_ADD)).carry_out
    assert model.evaluate(AluStimulus(0, 1, OP_SUB)).result == mask
    assert not model.evaluate(AluStimulus(0, 1, OP_SUB)).carry_out
    assert model.evaluate(AluStimulus(mask, 1, OP_SUB)).carry_out
    assert model.evaluate(AluStimulus(mask, 0, OP_AND)).result == 0
    assert model.evaluate(AluStimulus(mask, 0, OP_OR)).result == mask
    assert model.evaluate(AluStimulus(mask, mask, OP_XOR)).zero_flag
    assert model.evaluate(AluStimulus(mask, mask, OP_EQ)).result == 1
    assert model.evaluate(AluStimulus(0, mask, OP_LT)).result == 1


def test_model_detects_signed_overflow_on_add_and_sub() -> None:
    model = AluModel(8)
    add = model.evaluate(AluStimulus(0x7F, 0x01, OP_ADD))
    sub = model.evaluate(AluStimulus(0x80, 0x01, OP_SUB))

    assert add.result == 0x80
    assert add.overflow
    assert sub.result == 0x7F
    assert sub.overflow


def test_model_returns_zero_for_unknown_opcode() -> None:
    observed = AluModel(8).evaluate(AluStimulus(0xAA, 0x55, 0b111))
    assert observed == AluObservation(0, True, False, False)


def test_stimulus_is_seeded_and_contains_directed_prefix() -> None:
    first = alu_sequence(cycles=40, width=8, seed=21)
    second = alu_sequence(cycles=40, width=8, seed=21)
    other = alu_sequence(cycles=40, width=8, seed=22)

    assert first == second
    assert first != other
    assert [item.op for item in first[:7]] == [
        OP_ADD,
        OP_ADD,
        OP_ADD,
        OP_SUB,
        OP_SUB,
        OP_AND,
        OP_OR,
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"cycles": 0, "width": 8, "seed": 1},
        {"cycles": 10, "width": 0, "seed": 1},
    ],
)
def test_stimulus_rejects_invalid_arguments(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        alu_sequence(**arguments)


def test_checker_detects_an_injected_flag_fault() -> None:
    checker = AluChecker()
    checker.check(
        AluObservation(result=0, zero_flag=True, carry_out=False, overflow=False),
        AluObservation(result=0, zero_flag=False, carry_out=False, overflow=False),
    )
    assert not checker.passed
    assert checker.mismatches[0].field == "zero_flag"


def _have_simulator() -> bool:
    return shutil.which("verilator") is not None


@pytest.mark.simulation
@pytest.mark.parametrize("width", [1, 8, 32])
@pytest.mark.parametrize("sim", ["verilator", "icarus"])
def test_alu_simulation_passes(width: int, sim: str, tmp_path: Path) -> None:
    if not shutil.which("iverilog" if sim == "icarus" else sim):
        pytest.skip(f"{sim} is not installed")
    proc = subprocess.run(
        ["make", "sim", "DESIGN=alu", f"WIDTH={width}", f"SIM={sim}",
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
