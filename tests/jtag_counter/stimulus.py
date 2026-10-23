# SPDX-License-Identifier: Apache-2.0
"""Same seeded enables as the direct-pin counter testbench."""

from counter.stimulus import enable_sequence
from jtag_counter.model import COUNT, ID, RESET, STEP, STEPS


def commands(cycles: int, seed: int):
    if cycles <= 0:
        raise ValueError("cycles must be positive")
    yield "read", ID, None
    yield "write", RESET, 1
    yield "read", COUNT, None
    yield "read", STEPS, None
    for enable in enable_sequence(cycles=cycles, seed=seed):
        yield "write", STEP, int(enable)
        yield "read", COUNT, None
        yield "read", STEPS, None
