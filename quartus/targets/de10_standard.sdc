# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# SDC constraints for TARGET_BOARD=de10_standard (Terasic DE10-Standard,
# Cyclone V SX 5CSXFC6D6F31C6).
#
# Public Terasic material indicates the board also exposes a 50 MHz FPGA clock,
# HPS-based Cyclone V SoC fabric and the usual teaching-panel I/O. This template
# therefore keeps the same logical interface (`CLOCK_50`, `SW[*]`, `KEY[*]`,
# `LEDR[*]`) unless a course-specific board wrapper chooses narrower or renamed
# ports.
#
# IMPORTANT: the physical pin map for the final top-level wrapper must still be
# checked against the official DE10-Standard user manual before freezing a
# production .qsf. In particular, any GPIO/HSMC use and the exact exported KEY
# subset should be treated as board-specific and verified manually.

# --- Primary clocks -----------------------------------------------------------

create_clock -name clk_50 -period 20.000 [get_ports {CLOCK_50}]

# --- Derived clocks -----------------------------------------------------------

derive_pll_clocks
derive_clock_uncertainty

# --- Input delays -------------------------------------------------------------
# Default teaching profile: human-driven switches/buttons at 50 MHz. If a course
# binds additional peripherals (for example HSMC or a daughter card), extend
# this SDC rather than editing the RTL.

set_input_delay -clock clk_50 -max 2.000 [get_ports {SW[*]}]
set_input_delay -clock clk_50 -min 0.500 [get_ports {SW[*]}]
set_input_delay -clock clk_50 -max 2.000 [get_ports {KEY[*]}]
set_input_delay -clock clk_50 -min 0.500 [get_ports {KEY[*]}]

# --- Output delays ------------------------------------------------------------

set_output_delay -clock clk_50 -max 2.000 [get_ports {LEDR[*]}]
set_output_delay -clock clk_50 -min 0.500 [get_ports {LEDR[*]}]

# --- Exceptions ---------------------------------------------------------------
# JUSTIFICATION: SW and KEY are human-driven, asynchronous inputs. They enter
# the design through synchronisers, so the first capture flop is allowed to go
# metastable by design and its setup/hold relationship to clk_50 is not
# meaningful for implementation closure.

set_false_path -from [get_ports {SW[*]}] -to [all_registers]
set_false_path -from [get_ports {KEY[*]}] -to [all_registers]

# JUSTIFICATION: LEDs are observed by a human eye. The teaching use-case does
# not require sub-nanosecond output alignment on those pins.

set_false_path -from [all_registers] -to [get_ports {LEDR[*]}]
