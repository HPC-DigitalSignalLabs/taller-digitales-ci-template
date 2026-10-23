# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""CocoTB testbench for the smoke counter.

Thin by design: it drives pins and hands observations to the checker. All the
intelligence lives in ``model.py``, ``stimulus.py`` and ``checker.py``, which
know nothing about CocoTB. That is the project's testbench convention, and it is
what allows the same checker to run against silicon in Fase 3.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).parent))

from checker import CounterChecker  # noqa: E402
from stimulus import enable_sequence  # noqa: E402

CLOCK_PERIOD_NS = 20  # 50 MHz, matching the teaching-board Cyclone V fabric clock


async def _reset(dut) -> None:
    """Assert and release the asynchronous active-low reset."""
    dut.enable.value = 0
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def test_reset_clears_the_count(dut) -> None:
    """Reset must drive the count to zero regardless of prior state."""
    cocotb.start_soon(Clock(dut.clk, CLOCK_PERIOD_NS, units="ns").start())

    dut.rst_n.value = 1
    dut.enable.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)

    await _reset(dut)
    assert dut.count.value == 0, f"count is {int(dut.count.value)} after reset"


@cocotb.test()
async def test_counts_against_reference_model(dut) -> None:
    """Randomised, seeded run compared cycle by cycle against the model."""
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info("seed=%d cycles=%d (replay with TB_SEED=%d)", seed, cycles, seed)

    cocotb.start_soon(Clock(dut.clk, CLOCK_PERIOD_NS, units="ns").start())
    await _reset(dut)

    width = len(dut.count)
    assert width == int(os.environ.get("WIDTH", "8")), "RTL WIDTH override was not applied"
    checker = CounterChecker(width=width)
    checker.reset()

    for enable in enable_sequence(cycles=cycles, seed=seed):
        # Advance a delta step before driving a new value: the previous
        # iteration leaves us in the read-only phase, and some simulators
        # (Icarus) reject writes issued while still inside it.
        await Timer(1, units="step")
        dut.enable.value = int(enable)
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(enable, int(dut.count.value))

    assert checker.passed, checker.report()


@cocotb.test()
async def test_wraps_at_the_parameterised_width(dut) -> None:
    """The counter must wrap modulo 2**WIDTH, not saturate."""
    cocotb.start_soon(Clock(dut.clk, CLOCK_PERIOD_NS, units="ns").start())
    await _reset(dut)

    width = len(dut.count)
    # Exhaustive wrap is bounded; the maintained simulation matrix uses <=8.
    assert width <= 16, "exhaustive wrap demo is bounded to WIDTH<=16"
    modulus = 1 << width
    await FallingEdge(dut.clk)
    dut.enable.value = 1
    for _ in range(modulus):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)

    assert dut.count.value == 0, (
        f"expected wrap to 0 after {modulus} enabled cycles, "
        f"got {int(dut.count.value)}"
    )
