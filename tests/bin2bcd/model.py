# SPDX-License-Identifier: Apache-2.0
"""Independent arithmetic oracle (decimal division, not double-dabble)."""


def digit_count(width: int) -> int:
    if not 1 <= width <= 64:
        raise ValueError("width must be in 1..64")
    return (width + 2) // 3


def bin2bcd(value: int, width: int) -> int:
    digits = digit_count(width)
    if not 0 <= value < 1 << width:
        raise ValueError("value does not fit width")
    packed = 0
    for index in range(digits):
        value, digit = divmod(value, 10)
        packed |= digit << (4 * index)
    assert value == 0, "internal capacity error"
    return packed
