# SPDX-License-Identifier: Apache-2.0
"""Register contract for the real counter, independent of the wire driver."""

from counter.model import CounterModel
from transport import Transport

IDENTITY = 0x434E5431
ID, RESET, STEP, COUNT, STEPS = range(5)


class CounterRegisterModel(Transport):
    def __init__(self):
        super().__init__()
        self.counter = CounterModel(8)
        self.steps = 0

    def write_word(self, address, value):
        if address == RESET and value == 1:
            self.counter.reset()
            self.steps = 0
        elif address == STEP and value in (0, 1):
            self.counter.tick(bool(value))
            self.steps = (self.steps + 1) & 0xFFFF_FFFF
        else:
            raise ValueError("invalid counter register write")

    def read_word(self, address):
        if address == ID:
            return IDENTITY
        if address == COUNT:
            return self.counter.count
        if address == STEPS:
            return self.steps
        raise ValueError("invalid counter register read")
