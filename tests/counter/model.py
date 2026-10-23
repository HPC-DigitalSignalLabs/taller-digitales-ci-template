# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Reference model for the smoke counter.

Pure Python, no CocoTB, no knowledge of the simulator. Kept in its own module so
the checker can be validated against the model without a simulator running.
This separation is the project's testbench convention (the in-scope substitute
for UVM structure).
"""

from __future__ import annotations


class CounterModel:
    """Golden model of ``src/counter.sv``.

    Args:
        width: Counter width in bits.
    """

    def __init__(self, width: int = 8) -> None:
        if width <= 0:
            raise ValueError(f"width must be positive, got {width}")
        self.width = width
        self.count = 0

    @property
    def modulus(self) -> int:
        return 1 << self.width

    def reset(self) -> None:
        """Apply an asynchronous reset."""
        self.count = 0

    def tick(self, enable: bool) -> int:
        """Advance one clock edge and return the new count."""
        if enable:
            self.count = (self.count + 1) % self.modulus
        return self.count
