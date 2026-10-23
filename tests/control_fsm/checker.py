# SPDX-License-Identifier: Apache-2.0
"""Transport-independent output and mutual-exclusion checker."""


class ControlFsmChecker:
    def __init__(self) -> None:
        self.samples = 0

    def check(self, expected: tuple[int, int], actual: tuple[int, int]) -> None:
        assert actual != (1, 1), f"sample {self.samples}: busy and done overlap"
        assert actual == expected, f"sample {self.samples}: {actual} != {expected}"
        self.samples += 1
