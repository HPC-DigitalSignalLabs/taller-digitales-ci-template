# SPDX-License-Identifier: Apache-2.0
"""Thin CocoTB pin driver at the shared 50 MHz reference clock."""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from edge_detector.checker import EdgeDetectorChecker
from edge_detector.model import EdgeDetectorModel
from edge_detector.stimulus import edge_sequence


@cocotb.test()
async def test_selected_edges_and_reset(dut) -> None:
    rising = int(os.environ.get("RISING", "1"))
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info("RISING=%d seed=%d cycles=%d", rising, seed, cycles)
    dut.rst_n.value = 0
    dut.signal_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    model = EdgeDetectorModel(rising)
    checker = EdgeDetectorChecker()
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    # Finish with an active pulse so asynchronous reset is observable.
    for level in edge_sequence(cycles, seed) + [1 - rising, rising]:
        dut.signal_in.value = level
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(level), int(dut.pulse.value))
        await FallingEdge(dut.clk)
    assert int(dut.pulse.value) == 1
    dut.rst_n.value = 0
    await Timer(1, units="ns")
    assert int(dut.pulse.value) == 0
    model.reset()
    dut.signal_in.value = 1
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for level in [1, 1, 0, 0]:
        dut.signal_in.value = level
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(level), int(dut.pulse.value))
        await FallingEdge(dut.clk)
    assert checker.passed, checker.report()
