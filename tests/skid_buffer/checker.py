# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Transport-independent checker for skid buffer observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ComparableObservation(Protocol):
    """Ready/valid fields required from any simulation or hardware sample."""

    s_ready: bool
    m_valid: bool
    m_data: int


@dataclass(frozen=True)
class SkidMismatch:
    cycle: int
    field: str
    expected: int
    actual: int


class SkidBufferChecker:
    """Compare cycle observations and retain actionable failure details."""

    def __init__(self) -> None:
        self.cycle = 0
        self.mismatches: list[SkidMismatch] = []

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def check(
        self,
        expected: ComparableObservation,
        actual: ComparableObservation,
    ) -> None:
        for field in ("s_ready", "m_valid", "m_data"):
            expected_value = int(getattr(expected, field))
            actual_value = int(getattr(actual, field))
            if expected_value != actual_value:
                self.mismatches.append(
                    SkidMismatch(
                        cycle=self.cycle,
                        field=field,
                        expected=expected_value,
                        actual=actual_value,
                    )
                )
        self.cycle += 1

    def report(self) -> str:
        return "\n".join(
            f"cycle {item.cycle}: {item.field} expected {item.expected}, "
            f"got {item.actual}"
            for item in self.mismatches
        )
