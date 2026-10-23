# SPDX-License-Identifier: Apache-2.0
"""Sample-history reference model without simulator or transport imports."""


class EdgeDetectorModel:
    def __init__(self, rising: int = 1) -> None:
        if rising not in (0, 1):
            raise ValueError("rising must be 0 or 1")
        self.rising = rising
        self.reset()

    def reset(self) -> None:
        self.previous = 0

    def tick(self, level: int, rst_n: bool = True) -> int:
        if not rst_n:
            self.reset()
            return 0
        current = int(bool(level))
        event = current != self.previous
        self.previous = current
        return int(event)
