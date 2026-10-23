# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""CocoTB driver for the didactic combinational ALU."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ReadOnly, Timer

sys.path.insert(0, str(Path(__file__).parent))

from checker import AluChecker  # noqa: E402
from model import AluModel, AluObservation  # noqa: E402
from stimulus import alu_sequence  # noqa: E402


async def _drive_and_check(dut, stimulus, model: AluModel, checker: AluChecker) -> None:
    await Timer(1, units="step")
    dut.a.value = stimulus.a
    dut.b.value = stimulus.b
    dut.op.value = stimulus.op
    await Timer(1, units="step")
    await ReadOnly()

    expected = model.evaluate(stimulus)
    actual = AluObservation(
        result=int(dut.result.value),
        zero_flag=bool(dut.zero_flag.value),
        carry_out=bool(dut.carry_out.value),
        overflow=bool(dut.overflow.value),
    )
    checker.check(expected, actual)


@cocotb.test()
async def test_alu_matches_reference_model(dut) -> None:
    """Exercise directed corner cases plus replayable pseudo-random cases."""
    width = len(dut.a)
    assert width == int(os.environ.get("WIDTH", "8")), "RTL WIDTH override was not applied"
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info(
        "width=%d seed=%d cycles=%d (replay with TB_SEED=%d)",
        width,
        seed,
        cycles,
        seed,
    )

    model = AluModel(width)
    checker = AluChecker()
    for stimulus in alu_sequence(cycles=cycles, width=width, seed=seed):
        await _drive_and_check(dut, stimulus, model, checker)

    assert checker.passed, checker.report()
