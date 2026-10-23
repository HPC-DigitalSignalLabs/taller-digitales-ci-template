# SPDX-License-Identifier: Apache-2.0
import os

import cocotb
from cocotb.triggers import Timer

from bin2bcd.checker import Bin2BcdChecker
from bin2bcd.model import bin2bcd, digit_count
from bin2bcd.stimulus import binary_sequence


@cocotb.test()
async def test_unsigned_decimal_conversion(dut) -> None:
    width = int(os.environ.get("WIDTH", "8"))
    seed = int(os.environ.get("TB_SEED", "1234"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    dut._log.info("WIDTH=%d seed=%d cycles=%d", width, seed, cycles)
    assert len(dut.binary) == width
    assert len(dut.bcd) == 4 * digit_count(width)
    checker = Bin2BcdChecker(digit_count(width))
    for value in binary_sequence(width, cycles, seed):
        dut.binary.value = value
        await Timer(20, units="ns")
        checker.check(bin2bcd(value, width), int(dut.bcd.value))
