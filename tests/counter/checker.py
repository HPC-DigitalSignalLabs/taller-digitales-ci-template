# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Transport-agnostic checker for the smoke counter.

The load-bearing property: this module imports neither cocotb nor any hardware
driver. It compares an observed value stream against the reference model, so the
identical checker is used by the CocoTB testbench and, in Fase 3, by the JTAG
hardware validation. Reuse fraction is an OE3 metric; this is how it is earned.
"""

from __future__ import annotations

from dataclasses import dataclass

from model import CounterModel


@dataclass(frozen=True)
class Mismatch:
    """One cycle where hardware and model disagreed."""

    cycle: int
    enable: bool
    expected: int
    observed: int

    def __str__(self) -> str:
        return (
            f"cycle {self.cycle} (enable={int(self.enable)}): "
            f"expected {self.expected}, observed {self.observed}"
        )


class CounterChecker:
    """Compare an observed count stream against the reference model.

    Args:
        width: Counter width, must match the DUT parameter.
    """

    def __init__(self, width: int = 8) -> None:
        self.model = CounterModel(width=width)
        self.mismatches: list[Mismatch] = []
        self._cycle = 0

    def reset(self) -> None:
        self.model.reset()

    def check(self, enable: bool, observed: int) -> None:
        """Advance the model one cycle and compare against the observation."""
        expected = self.model.tick(enable)
        if observed != expected:
            self.mismatches.append(
                Mismatch(
                    cycle=self._cycle,
                    enable=enable,
                    expected=expected,
                    observed=observed,
                )
            )
        self._cycle += 1

    def report(self) -> str:
        """Human-readable summary; empty string when everything matched."""
        if not self.mismatches:
            return ""
        head = "\n".join(str(m) for m in self.mismatches[:10])
        extra = len(self.mismatches) - 10
        if extra > 0:
            head += f"\n... and {extra} more"
        return f"{len(self.mismatches)} mismatch(es) over {self._cycle} cycles:\n{head}"

    @property
    def passed(self) -> bool:
        return not self.mismatches
