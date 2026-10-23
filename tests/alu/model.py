# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Pure Python reference model for the didactic ALU."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

OP_ADD = 0b000
OP_SUB = 0b001
OP_AND = 0b010
OP_OR = 0b011
OP_XOR = 0b100
OP_EQ = 0b101
OP_LT = 0b110
VALID_OPS = {OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_EQ, OP_LT}


class AluInput(Protocol):
    """Minimum transaction interface consumed by the model."""

    a: int
    b: int
    op: int


@dataclass(frozen=True)
class AluObservation:
    """Expected ALU outputs for one stimulus."""

    result: int
    zero_flag: bool
    carry_out: bool
    overflow: bool


class AluModel:
    """Stateless golden model of ``lib/alu/alu.sv``."""

    def __init__(self, width: int = 8) -> None:
        if width < 1:
            raise ValueError("width must be positive")
        self.width = width
        self.mask = (1 << width) - 1
        self.sign_bit = 1 << (width - 1)

    def evaluate(self, stimulus: AluInput) -> AluObservation:
        a = stimulus.a & self.mask
        b = stimulus.b & self.mask
        op = stimulus.op
        result = 0
        carry_out = False
        overflow = False

        if op not in VALID_OPS:
            return AluObservation(0, True, False, False)

        if op == OP_ADD:
            total = a + b
            result = total & self.mask
            carry_out = bool((total >> self.width) & 0x1)
            overflow = bool((~(a ^ b) & (a ^ result) & self.sign_bit) != 0)
        elif op == OP_SUB:
            result = (a - b) & self.mask
            carry_out = a >= b
            overflow = bool(((a ^ b) & (a ^ result) & self.sign_bit) != 0)
        elif op == OP_AND:
            result = a & b
        elif op == OP_OR:
            result = a | b
        elif op == OP_XOR:
            result = a ^ b
        elif op == OP_EQ:
            result = int(a == b)
        elif op == OP_LT:
            result = int(a < b)

        return AluObservation(
            result=result,
            zero_flag=result == 0,
            carry_out=carry_out,
            overflow=overflow,
        )
