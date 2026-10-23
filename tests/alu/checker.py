# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Transport-independent checker for ALU observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ComparableObservation(Protocol):
    """Fields required from any simulation or hardware sample."""

    result: int
    zero_flag: bool
    carry_out: bool
    overflow: bool


@dataclass(frozen=True)
class AluMismatch:
    index: int
    field: str
    expected: int
    actual: int


class AluChecker:
    """Compare ALU observations and retain actionable failure details."""

    def __init__(self) -> None:
        self.index = 0
        self.mismatches: list[AluMismatch] = []

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def check(
        self,
        expected: ComparableObservation,
        actual: ComparableObservation,
    ) -> None:
        for field in ("result", "zero_flag", "carry_out", "overflow"):
            expected_value = int(getattr(expected, field))
            actual_value = int(getattr(actual, field))
            if expected_value != actual_value:
                self.mismatches.append(
                    AluMismatch(
                        index=self.index,
                        field=field,
                        expected=expected_value,
                        actual=actual_value,
                    )
                )
        self.index += 1

    def report(self) -> str:
        return "\n".join(
            f"stimulus {item.index}: {item.field} expected {item.expected}, "
            f"got {item.actual}"
            for item in self.mismatches
        )
