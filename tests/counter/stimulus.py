# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Seeded stimulus generator for the smoke counter.

Randomisation is constrained and the seed is explicit, so any run is replayable
from its logged seed. An unseeded random testbench is a flaky test waiting to
happen, and a flaky rate above zero fails OE2.
"""

from __future__ import annotations

import random
from typing import Iterator


def enable_sequence(cycles: int, seed: int, density: float = 0.6) -> Iterator[bool]:
    """Yield ``cycles`` enable values.

    Args:
        cycles: Number of clock cycles to generate.
        seed: Seed for the local RNG. Logged by the testbench and replayable.
        density: Probability that enable is asserted on a given cycle.

    Yields:
        The enable value for each cycle.
    """
    if cycles < 0:
        raise ValueError(f"cycles must be non-negative, got {cycles}")
    if not 0.0 <= density <= 1.0:
        raise ValueError(f"density must be in [0, 1], got {density}")

    # A local Random instance, never the module-level one: a testbench must not
    # perturb or be perturbed by global RNG state.
    rng = random.Random(seed)
    for _ in range(cycles):
        yield rng.random() < density
