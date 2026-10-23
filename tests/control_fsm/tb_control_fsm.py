# SPDX-License-Identifier: Apache-2.0
"""Pin driver; asynchronous reset asserted away from a 50 MHz rising edge."""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from control_fsm.checker import ControlFsmChecker
from control_fsm.model import ControlFsmModel
from control_fsm.stimulus import control_sequence


@cocotb.test()
async def test_transactions(dut) -> None:
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info("seed=%d cycles=%d", seed, cycles)
    dut.rst_n.value = 0
    dut.start.value = dut.finish.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    model, checker = ControlFsmModel(), ControlFsmChecker()
    for start, finish, rst_n in control_sequence(cycles, seed):
        await FallingEdge(dut.clk)
        dut.start.value, dut.finish.value = start, finish
        dut.rst_n.value = rst_n
        await Timer(1, units="ns")
        if not rst_n:
            assert (int(dut.busy.value), int(dut.done.value)) == (0, 0)
        else:
            assert (int(dut.busy.value), int(dut.done.value)) == (
                int(model.running), int(model.completed))
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(start, finish, bool(rst_n)),
                      (int(dut.busy.value), int(dut.done.value)))
