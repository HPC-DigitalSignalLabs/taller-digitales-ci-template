# SPDX-License-Identifier: Apache-2.0
import os

import cocotb
from cocotb.triggers import Timer

from bcd_to_7seg.checker import BcdTo7SegChecker
from bcd_to_7seg.model import bcd_to_7seg
from bcd_to_7seg.stimulus import display_sequence


@cocotb.test()
async def test_digits_polarity_and_invalid_blanking(dut) -> None:
    seed = int(os.environ.get("TB_SEED", "1234"))
    dut._log.info("seed=%d; exhaustive 16 inputs x 2 polarities", seed)
    checker = BcdTo7SegChecker()
    for value, polarity in display_sequence(seed):
        dut.bcd.value, dut.active_low.value = value, polarity
        await Timer(20, units="ns")
        checker.check(bcd_to_7seg(value, polarity), int(dut.seg.value))
