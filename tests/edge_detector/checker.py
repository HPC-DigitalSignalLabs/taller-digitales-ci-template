# SPDX-License-Identifier: Apache-2.0
"""The event checker accepts observations from any transport."""


class EdgeDetectorChecker:
    def __init__(self) -> None:
        self.cycle = 0
        self.mismatches: list[str] = []

    def check(self, expected: int, actual: int) -> None:
        if expected != actual:
            self.mismatches.append(
                f"cycle {self.cycle}: pulse expected {expected}, observed {actual}"
            )
        self.cycle += 1

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def report(self) -> str:
        return "\n".join(self.mismatches)
