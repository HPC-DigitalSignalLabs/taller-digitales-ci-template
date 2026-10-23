# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""CocoTB driver for the ready/valid skid buffer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).parent))

from checker import SkidBufferChecker  # noqa: E402
from model import SkidBufferModel, SkidObservation  # noqa: E402
from stimulus import SkidStimulus, skid_sequence  # noqa: E402

CLOCK_PERIOD_NS = 20


async def _reset(dut) -> None:
    dut.s_valid.value = 0
    dut.s_data.value = 0
    dut.m_ready.value = 0
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def _drive_and_check(
    dut,
    stimulus: SkidStimulus,
    model: SkidBufferModel,
    checker: SkidBufferChecker,
) -> None:
    dut.s_valid.value = int(stimulus.valid)
    dut.s_data.value = stimulus.data
    dut.m_ready.value = int(stimulus.ready)
    await Timer(1, units="step")
    await ReadOnly()

    expected = model.observe(stimulus)
    actual = SkidObservation(
        s_ready=bool(dut.s_ready.value),
        m_valid=bool(dut.m_valid.value),
        m_data=int(dut.m_data.value),
    )
    checker.check(expected, actual)

    await RisingEdge(dut.clk)
    model.tick(stimulus)
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_ready_valid_under_backpressure(dut) -> None:
    """Check fill, stalls, full-throughput transfers and drain."""
    width = len(dut.s_data)
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info(
        "width=%d seed=%d cycles=%d (replay with TB_SEED=%d)",
        width,
        seed,
        cycles,
        seed,
    )

    cocotb.start_soon(Clock(dut.clk, CLOCK_PERIOD_NS, units="ns").start())
    await _reset(dut)

    model = SkidBufferModel(width)
    checker = SkidBufferChecker()
    for stimulus in skid_sequence(cycles=cycles, width=width, seed=seed):
        await _drive_and_check(dut, stimulus, model, checker)

    # Drain the final buffered item and then verify that valid drops.
    for _ in range(2):
        await _drive_and_check(
            dut,
            SkidStimulus(valid=False, data=0, ready=True),
            model,
            checker,
        )

    assert checker.passed, checker.report()
