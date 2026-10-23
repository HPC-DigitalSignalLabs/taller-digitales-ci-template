# SPDX-License-Identifier: Apache-2.0
"""Digital sampling model, deliberately making no metastability claim."""


class Sync2ffModel:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stage1 = 0
        self.output = 0

    def tick(self, level: int, rst_n: bool = True) -> int:
        if not rst_n:
            self.reset()
        else:
            self.output, self.stage1 = self.stage1, int(bool(level))
        return self.output
