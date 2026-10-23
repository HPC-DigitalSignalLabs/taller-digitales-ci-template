# SPDX-License-Identifier: Apache-2.0
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from enabled_register.checker import EnabledRegisterChecker
from enabled_register.model import EnabledRegisterModel
from enabled_register.stimulus import register_sequence


@cocotb.test()
async def test_load_hold_reset(dut) -> None:
    width = int(os.environ.get("WIDTH", "8"))
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    assert len(dut.data_in) == len(dut.data_out) == width
    dut._log.info("WIDTH=%d seed=%d cycles=%d", width, seed, cycles)
    dut.rst_n.value = 0
    dut.enable.value = dut.data_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    model, checker = EnabledRegisterModel(width), EnabledRegisterChecker()
    for data, enable, rst_n in register_sequence(width, cycles, seed):
        await FallingEdge(dut.clk)
        dut.data_in.value, dut.enable.value, dut.rst_n.value = data, enable, rst_n
        await Timer(1, units="ns")
        if not rst_n:
            assert int(dut.data_out.value) == 0
        else:
            assert int(dut.data_out.value) == model.value, "data changed between edges"
        await RisingEdge(dut.clk)
        await ReadOnly()
        checker.check(model.tick(data, enable, bool(rst_n)), int(dut.data_out.value))
