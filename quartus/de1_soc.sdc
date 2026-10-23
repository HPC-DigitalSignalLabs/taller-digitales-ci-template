# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# SDC constraints for the DE1-SoC (Cyclone V 5CSEMA5F31C6).
#
# Legacy compatibility copy. New target-selectable Quartus flows should prefer
# quartus/targets/de1_soc.sdc via TARGET_BOARD=de1_soc.
#
# Project convention, enforced by review:
#   * create_clock for every primary clock
#   * derive_pll_clocks and derive_clock_uncertainty for Cyclone V PLLs
#   * explicit set_input_delay / set_output_delay on all I/O
#   * set_false_path and set_multicycle_path ONLY with a written justification
#     in this file
#
# A timing report without its SDC and its commit is not evidence.

# --- Primary clocks -----------------------------------------------------------
# The DE1-SoC provides four 50 MHz fabric clocks (CLOCK_50, CLOCK2_50, CLOCK3_50,
# CLOCK4_50). Only the one actually used by the design is constrained here.

create_clock -name clk_50 -period 20.000 [get_ports {CLOCK_50}]

# --- Derived clocks -----------------------------------------------------------
# Must come after create_clock. derive_clock_uncertainty models jitter and is
# not optional: without it, slack is optimistic and hardware disagrees with STA,
# which shows up as an |STA predicted - measured| divergence in OE4.

derive_pll_clocks
derive_clock_uncertainty

# --- Input delays -------------------------------------------------------------
# Switches and buttons are asynchronous to clk_50. They are constrained anyway
# so that the paths are analysed rather than silently unconstrained; the
# synchronisers in the design are what actually make them safe.

set_input_delay -clock clk_50 -max 2.000 [get_ports {SW[*]}]
set_input_delay -clock clk_50 -min 0.500 [get_ports {SW[*]}]
set_input_delay -clock clk_50 -max 2.000 [get_ports {KEY[*]}]
set_input_delay -clock clk_50 -min 0.500 [get_ports {KEY[*]}]

# --- Output delays ------------------------------------------------------------

set_output_delay -clock clk_50 -max 2.000 [get_ports {LEDR[*]}]
set_output_delay -clock clk_50 -min 0.500 [get_ports {LEDR[*]}]

# --- Exceptions ---------------------------------------------------------------
# Each exception below carries its justification. An unjustified exception is a
# way of hiding a timing failure, and review rejects it.
#
# JUSTIFICATION: SW and KEY are human-driven, asynchronous inputs. They enter
# the design exclusively through two-flop synchronisers, so the first capture
# flop is permitted to go metastable by design and its setup/hold relationship
# to clk_50 is meaningless. Cutting the path prevents the fitter from spending
# effort on an analysis that carries no information. The synchronisers
# themselves remain fully constrained.

set_false_path -from [get_ports {SW[*]}] -to [all_registers]
set_false_path -from [get_ports {KEY[*]}] -to [all_registers]

# JUSTIFICATION: LEDs are observed by a human eye. Sub-nanosecond skew on these
# outputs has no observable effect, and constraining them tightly would consume
# routing resources that the datapath needs.

set_false_path -from [all_registers] -to [get_ports {LEDR[*]}]
