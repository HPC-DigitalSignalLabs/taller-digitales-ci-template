# SPDX-License-Identifier: Apache-2.0
"""Pin driver: deterministic digital CDC latency and asynchronous reset."""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from sync_2ff.checker import Sync2ffChecker
from sync_2ff.model import Sync2ffModel
from sync_2ff.stimulus import level_sequence


@cocotb.test()
async def test_sampled_latency_and_reset(dut) -> None:
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info("digital-only CDC: seed=%d cycles=%d", seed, cycles)
    dut.rst_n.value = 0
    dut.async_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    model = Sync2ffModel()
    checker = Sync2ffChecker()
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for level in level_sequence(cycles, seed) + [1, 1, 1]:
        # Non-edge phase avoids a simulator race. It does not test analog CDC.
        await Timer(3, units="ns")
        dut.async_in.value = level
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(level), int(dut.sync_out.value))
        await FallingEdge(dut.clk)
    assert int(dut.sync_out.value) == 1
    # Reset populated stages between clock edges, then flush stale history.
    dut.rst_n.value = 0
    await Timer(1, units="ns")
    assert int(dut.sync_out.value) == 0
    model.reset()
    dut.async_in.value = 0
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(3):
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(0), int(dut.sync_out.value))
        await FallingEdge(dut.clk)
    assert checker.passed, checker.report()
