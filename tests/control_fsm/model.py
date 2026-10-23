# SPDX-License-Identifier: Apache-2.0
"""Transaction-level reference, independent of RTL state encoding."""


class ControlFsmModel:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.completed = False

    def tick(self, start: int, finish: int, rst_n: bool = True) -> tuple[int, int]:
        if not rst_n or self.completed:
            self.reset()
        elif self.running:
            if finish:
                self.running, self.completed = False, True
        elif start:
            self.running = True
        return int(self.running), int(self.completed)
