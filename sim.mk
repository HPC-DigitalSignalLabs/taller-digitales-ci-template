# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# CocoTB simulation include, kept separate from the top-level Makefile.
#
# cocotb's Makefile.sim defines its own targets and variables; including it in
# the main Makefile would collide with `test`, `clean` and friends. Isolating it
# here keeps both files readable and keeps the top-level interface stable.
#
# `SIM=questa` is intentionally delegated to cocotb's upstream Siemens flow too,
# but only as an optional local path for a licensed lab machine. CI, Docker and
# Kabré stay on the open-source simulators documented in the top-level Makefile.

TEMPLATE_ROOT := $(CURDIR)
.DEFAULT_GOAL := sim

DESIGN        ?= counter
TOPLEVEL      ?= $(if $(filter skid_buffer,$(DESIGN)),skid_buffer_sim_top,$(DESIGN))
MODULE        ?= tb_$(DESIGN)
SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
WIDTH         ?= $(if $(filter sync_2ff edge_detector,$(DESIGN)),1,8)
RISING        ?= 1

VERILOG_SOURCES ?= $(if $(filter counter,$(DESIGN)),$(TEMPLATE_ROOT)/src/counter.sv,$(if $(filter alu skid_buffer sync_2ff edge_detector control_fsm enabled_register bin2bcd bcd_to_7seg,$(DESIGN)),$(TEMPLATE_ROOT)/lib/$(DESIGN)/$(DESIGN).sv,$(wildcard $(TEMPLATE_ROOT)/src/*.sv) $(wildcard $(TEMPLATE_ROOT)/lib/*/*.sv))) $(wildcard $(TEMPLATE_ROOT)/tests/$(DESIGN)/*.sv)

EXTRA_ARGS += $(if $(filter verilator,$(SIM)),--trace --trace-structs -Wall)
SIM_ARGS += $(if $(filter verilator,$(SIM)),--trace-file $(SIM_BUILD)/dump.vcd)
COMPILE_ARGS += $(if $(filter skid_buffer_sim_top,$(TOPLEVEL)),-DSKID_WIDTH=$(WIDTH))
# WIDTH used to be exported only to Python: counter/ALU silently simulated w8.
# Apply parameters to the elaborator, not just to the reference model.
PARAM_NAME = $(if $(filter counter alu enabled_register bin2bcd,$(DESIGN)),WIDTH,$(if $(filter edge_detector,$(DESIGN)),RISING))
PARAM_VALUE = $(if $(filter edge_detector,$(DESIGN)),$(RISING),$(WIDTH))
VERILATOR_PARAM_VALUE = $(if $(filter edge_detector,$(DESIGN)),"1'b$(RISING)",$(WIDTH))
ifneq ($(PARAM_NAME),)
COMPILE_ARGS += $(if $(filter verilator,$(SIM)),-G$(PARAM_NAME)=$(VERILATOR_PARAM_VALUE))
COMPILE_ARGS += $(if $(filter icarus,$(SIM)),-P$(TOPLEVEL).$(PARAM_NAME)=$(PARAM_VALUE))
SIM_ARGS += $(if $(filter questa,$(SIM)),-g$(PARAM_NAME)=$(PARAM_VALUE))
endif

COCOTB_HDL_TIMEUNIT      ?= 1ns
COCOTB_HDL_TIMEPRECISION ?= 1ps
COCOTB_RESULTS_FILE      ?= $(TEMPLATE_ROOT)/results/cocotb-$(SIM)-$(DESIGN)-w$(WIDTH)$(if $(filter edge_detector,$(DESIGN)),-r$(RISING)).xml

$(shell mkdir -p $(dir $(COCOTB_RESULTS_FILE)))

export PYTHONPATH := $(TEMPLATE_ROOT)/tests/$(DESIGN):$(TEMPLATE_ROOT)/tests/common:$(TEMPLATE_ROOT)/tests:$(PYTHONPATH)
export WIDTH
export RISING

.PHONY: check-sim-prereqs

check-sim-prereqs:
ifeq ($(SIM),questa)
	@if [ -n "$$CI" ] || [ -n "$$GITHUB_ACTIONS" ] || [ -n "$$SLURM_JOB_ID" ] || [ -n "$$APPTAINER_NAME" ] || [ -n "$$SINGULARITY_NAME" ] || [ -f /.dockerenv ]; then \
		echo "ERROR: SIM=questa is supported only on a local lab machine with a Siemens license."; \
		echo "       The DevContainer/Docker image, GitHub Actions and Kabré remain in the open verification domain."; \
		echo "       Use SIM=verilator or SIM=icarus there."; \
		exit 2; \
	fi
	@if ! command -v qrun >/dev/null 2>&1 && ! command -v vsim >/dev/null 2>&1; then \
		echo "ERROR: SIM=questa selected, but neither qrun nor vsim is available on PATH."; \
		echo "       Initialise the local Questa/ModelSim environment first, or switch to SIM=verilator/SIM=icarus."; \
		echo "       This proprietary path is optional and never runs in CI or on Kabré."; \
		exit 2; \
	fi
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
