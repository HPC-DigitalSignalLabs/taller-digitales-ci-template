# SPDX-License-Identifier: Apache-2.0
"""Compare the actual seven output bits, independent of transport."""


class BcdTo7SegChecker:
    def __init__(self) -> None:
        self.samples = 0

    def check(self, expected: int, actual: int) -> None:
        assert actual == expected, f"sample {self.samples}: {actual:07b} != {expected:07b}"
        self.samples += 1
