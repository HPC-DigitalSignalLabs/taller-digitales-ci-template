# SPDX-License-Identifier: Apache-2.0
"""Directed ignored/held requests, then seeded stress. (start, finish, rst_n)."""
import random


def control_sequence(cycles: int, seed: int) -> list[tuple[int, int, int]]:
    if cycles < 1:
        raise ValueError("cycles must be positive")
    directed = [
        (0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1), (1, 1, 1),
        (1, 1, 1), (1, 1, 1), (1, 1, 1), (0, 0, 1), (0, 0, 1),
        (1, 0, 1), (1, 1, 0), (0, 1, 1),
        (1, 0, 1), (0, 1, 1), (1, 0, 0), (0, 0, 1),
    ]
    rng = random.Random(seed)
    return directed + [
        (rng.randrange(2), rng.randrange(2), int(rng.randrange(12) != 0))
        for _ in range(cycles)
    ]
