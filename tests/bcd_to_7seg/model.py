# SPDX-License-Identifier: Apache-2.0
"""Human-readable segment sets, independent of the RTL binary lookup table."""
SEGMENTS = ("abcdef", "bc", "abdeg", "abcdg", "bcfg",
            "acdfg", "acdefg", "abc", "abcdefg", "abcdfg")


def bcd_to_7seg(bcd: int, active_low: int = 1) -> int:
    if not 0 <= bcd <= 15 or active_low not in (0, 1):
        raise ValueError("expected four-bit BCD input and binary polarity")
    lit = sum(1 << ("abcdefg".index(segment))
              for segment in (SEGMENTS[bcd] if bcd < 10 else ""))
    return lit ^ (0x7F if active_low else 0)
