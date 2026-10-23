# SPDX-License-Identifier: Apache-2.0
"""Directed stable levels, adjacent transitions and seeded random samples."""
import random


def edge_sequence(cycles: int, seed: int) -> list[int]:
    if cycles < 1:
        raise ValueError("cycles must be positive")
    prefix = [1, 1, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1]
    rng = random.Random(seed)
    return (prefix + [rng.randrange(2) for _ in range(cycles)])[:cycles]
