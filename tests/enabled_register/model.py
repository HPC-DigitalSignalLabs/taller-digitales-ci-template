# SPDX-License-Identifier: Apache-2.0
"""Width-checked unsigned storage; reset has priority over enable."""


class EnabledRegisterModel:
    def __init__(self, width: int = 8) -> None:
        if width < 1:
            raise ValueError("width must be positive")
        self.width = width
        self.reset()

    def reset(self) -> None:
        self.value = 0

    def tick(self, data: int, enable: int, rst_n: bool = True) -> int:
        if not 0 <= data < 1 << self.width:
            raise ValueError("data does not fit width")
        if not rst_n:
            self.reset()
        elif enable:
            self.value = data
        return self.value
