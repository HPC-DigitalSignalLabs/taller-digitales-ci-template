# SPDX-License-Identifier: Apache-2.0
"""Exhaustive 32 cases, followed by seeded shuffled transitions."""
import random


def display_sequence(seed: int) -> list[tuple[int, int]]:
    exhaustive = [(bcd, polarity) for polarity in (0, 1) for bcd in range(16)]
    shuffled = exhaustive.copy()
    random.Random(seed).shuffle(shuffled)
    return exhaustive + shuffled
