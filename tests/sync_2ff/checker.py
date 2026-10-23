# SPDX-License-Identifier: Apache-2.0
"""Transport-independent comparison of sampled digital output."""


class Sync2ffChecker:
    def __init__(self) -> None:
        self.cycle = 0
        self.mismatches: list[str] = []

    def check(self, expected: int, actual: int) -> None:
        if expected != actual:
            self.mismatches.append(
                f"cycle {self.cycle}: expected {expected}, observed {actual}"
            )
        self.cycle += 1

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def report(self) -> str:
        return "\n".join(self.mismatches)
