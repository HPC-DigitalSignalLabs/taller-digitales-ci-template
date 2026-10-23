# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Deterministic stimuli for a ready/valid skid buffer."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class SkidStimulus:
    """Values driven during one clock cycle."""

    valid: bool
    data: int
    ready: bool


def skid_sequence(
    *,
    cycles: int,
    width: int,
    seed: int,
) -> list[SkidStimulus]:
    """Build a replayable sequence with bursts and downstream backpressure."""
    if cycles < 1:
        raise ValueError("cycles must be positive")
    if width < 1:
        raise ValueError("width must be positive")

    rng = Random(seed)
    mask = (1 << width) - 1
    sequence: list[SkidStimulus] = []

    # Directed prefix: capture a word on the first stalled cycle, hold the
    # source payload while blocked, drain, then exercise full-rate bypass.
    directed = [
        SkidStimulus(True, 1 & mask, False),
        SkidStimulus(True, 2 & mask, False),
        SkidStimulus(True, 2 & mask, False),
        SkidStimulus(True, 2 & mask, False),
        SkidStimulus(True, 2 & mask, True),
        SkidStimulus(True, 2 & mask, True),
        SkidStimulus(True, 3 & mask, True),
        SkidStimulus(False, 0, True),
    ]
    sequence.extend(directed[:cycles])

    while len(sequence) < cycles:
        sequence.append(
            SkidStimulus(
                valid=rng.random() < 0.72,
                data=rng.randrange(1 << width),
                ready=rng.random() < 0.58,
            )
        )
    return sequence
