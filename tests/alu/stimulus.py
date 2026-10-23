# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Deterministic stimuli for the didactic ALU."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

OP_ADD = 0b000
OP_SUB = 0b001
OP_AND = 0b010
OP_OR = 0b011
OP_XOR = 0b100
OP_EQ = 0b101
OP_LT = 0b110


@dataclass(frozen=True)
class AluStimulus:
    """Values driven for one combinational ALU check."""

    a: int
    b: int
    op: int


def alu_sequence(
    *,
    cycles: int,
    width: int,
    seed: int,
) -> list[AluStimulus]:
    """Build a replayable directed-plus-random sequence."""
    if cycles < 1:
        raise ValueError("cycles must be positive")
    if width < 1:
        raise ValueError("width must be positive")

    rng = Random(seed)
    mask = (1 << width) - 1
    max_signed = (1 << (width - 1)) - 1
    min_signed = 1 << (width - 1)
    pattern_a = int("10" * ((width + 1) // 2), 2) & mask
    pattern_b = int("01" * ((width + 1) // 2), 2) & mask

    sequence = [
        AluStimulus(max_signed, 1, OP_ADD),        # signed overflow
        AluStimulus(mask, 1, OP_ADD),              # carry out
        AluStimulus(0, 0, OP_ADD),                 # zero flag
        AluStimulus(min_signed, 1, OP_SUB),        # signed overflow
        AluStimulus(0, 1, OP_SUB),                 # borrow / wrapped result
        AluStimulus(pattern_a, pattern_b, OP_AND),
        AluStimulus(pattern_a, pattern_b, OP_OR),
        AluStimulus(pattern_a, pattern_a, OP_XOR),  # zero result
        AluStimulus(pattern_a, pattern_a, OP_EQ),
        AluStimulus(pattern_a, pattern_b, OP_EQ),
        AluStimulus(0, mask, OP_LT),
        AluStimulus(mask, 0, OP_LT),
    ][:cycles]

    while len(sequence) < cycles:
        sequence.append(
            AluStimulus(
                a=rng.randrange(1 << width),
                b=rng.randrange(1 << width),
                op=rng.randrange(0, 7),
            )
        )
    return sequence
