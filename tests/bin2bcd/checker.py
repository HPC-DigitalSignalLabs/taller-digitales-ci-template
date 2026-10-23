# SPDX-License-Identifier: Apache-2.0
"""Reject invalid digits and compare packed BCD without transport imports."""


class Bin2BcdChecker:
    def __init__(self, digits: int) -> None:
        if digits < 1:
            raise ValueError("digits must be positive")
        self.digits = digits
        self.samples = 0

    def check(self, expected: int, actual: int) -> None:
        assert 0 <= actual < 1 << (4 * self.digits), "output exceeds digit capacity"
        assert all((actual >> (4 * i)) & 15 <= 9 for i in range(self.digits)), \
            f"sample {self.samples}: non-decimal digit in {actual:x}"
        assert actual == expected, f"sample {self.samples}: {actual:x} != {expected:x}"
        self.samples += 1
