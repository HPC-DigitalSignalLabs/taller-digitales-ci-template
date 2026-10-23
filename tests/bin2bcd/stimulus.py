# SPDX-License-Identifier: Apache-2.0
"""All small inputs; decimal and binary boundaries plus seeded wide inputs."""
import random

from .model import digit_count


def binary_sequence(width: int, cycles: int, seed: int) -> list[int]:
    digit_count(width)
    if cycles < 1:
        raise ValueError("cycles must be positive")
    if width <= 10:
        return list(range(1 << width))
    maximum = (1 << width) - 1
    values = {0, 1, maximum, maximum - 1}
    for base in (2, 10):
        boundary = 1
        while boundary <= maximum:
            values.update(x for x in (boundary - 1, boundary, boundary + 1)
                          if 0 <= x <= maximum)
            boundary *= base
    rng = random.Random(seed)
    return sorted(values) + [rng.getrandbits(width) for _ in range(cycles)]
