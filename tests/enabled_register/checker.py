# SPDX-License-Identifier: Apache-2.0
"""Transport-independent stored-value comparison."""


class EnabledRegisterChecker:
    def __init__(self) -> None:
        self.samples = 0

    def check(self, expected: int, actual: int) -> None:
        assert actual == expected, f"sample {self.samples}: {actual} != {expected}"
        self.samples += 1
