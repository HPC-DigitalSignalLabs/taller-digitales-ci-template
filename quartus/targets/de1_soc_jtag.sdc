# SPDX-License-Identifier: Apache-2.0
create_clock -name clk_50 -period 20.000 [get_ports {CLOCK_50}]
derive_pll_clocks
derive_clock_uncertainty

# KEY0_N only asserts the two-flop reset-release synchronizer asynchronously.
# Its release is synchronized; downstream recovery/removal paths remain timed.
set_false_path -from [get_ports {KEY0_N}] -to [get_registers {reset_release[*]}]

# LEDs are static, human-observed diagnostics, not a clocked external interface.
# There is no receiver setup/hold requirement. Do not exempt internal datapaths.
set_false_path -to [get_ports {LEDR[*]}]
