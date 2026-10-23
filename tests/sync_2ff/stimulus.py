# SPDX-License-Identifier: Apache-2.0
"""Sampled single-bit levels with directed transitions and a local RNG."""
import random


def level_sequence(cycles: int, seed: int) -> list[int]:
    if cycles < 1:
        raise ValueError("cycles must be positive")
    prefix = [0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 0]
    rng = random.Random(seed)
    return (prefix + [rng.randrange(2) for _ in range(cycles)])[:cycles]
