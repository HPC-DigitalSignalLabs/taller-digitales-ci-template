# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Pure Python reference model for the one-entry skid buffer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SkidInput(Protocol):
    """Minimum transaction interface consumed by the model."""

    valid: bool
    data: int
    ready: bool


@dataclass(frozen=True)
class SkidObservation:
    """Ready/valid outputs observed during one cycle."""

    s_ready: bool
    m_valid: bool
    m_data: int


class SkidBufferModel:
    """Cycle-accurate model with no simulator or transport dependency."""

    def __init__(self, width: int) -> None:
        if width < 1:
            raise ValueError("width must be positive")
        self.width = width
        self.mask = (1 << width) - 1
        self.reset()

    def reset(self) -> None:
        self.full = False
        self.data = 0

    def observe(self, stimulus: SkidInput) -> SkidObservation:
        return SkidObservation(
            s_ready=not self.full,
            m_valid=self.full or stimulus.valid,
            m_data=self.data if self.full else (stimulus.data & self.mask),
        )

    def tick(self, stimulus: SkidInput) -> None:
        if self.full:
            if stimulus.ready:
                self.full = False
        elif stimulus.valid and not stimulus.ready:
            self.full = True
            self.data = stimulus.data & self.mask
