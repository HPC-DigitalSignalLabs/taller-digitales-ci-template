# SPDX-License-Identifier: Apache-2.0
"""(data, enable, rst_n), including hold, maximum, and reset/load conflict."""
import random


def register_sequence(width: int, cycles: int, seed: int) -> list[tuple[int, int, int]]:
    if width < 1 or cycles < 1:
        raise ValueError("width and cycles must be positive")
    maximum = (1 << width) - 1
    rng = random.Random(seed)
    return [
        (maximum, 0, 1), (maximum, 1, 1), (0, 0, 1), (0, 0, 1),
        (maximum, 1, 0), (maximum, 0, 1), (maximum, 1, 1), (0, 1, 1),
        (maximum, 1, 1), (maximum, 0, 0), (0, 0, 1),
    ] + [(rng.getrandbits(width), rng.randrange(2), int(rng.randrange(12) != 0))
         for _ in range(cycles)]
