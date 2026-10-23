# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# Single build abstraction for the open-source verification domain.
#
# The same command must behave identically on the student's machine, on Kabré
# and on a hosted runner. That is an OE2 acceptance criterion, so anything that
# would make behaviour depend on the host does not belong in this file.
#
#   make sim  DESIGN=counter    build with Verilator and run the CocoTB testbench
#   make sim  SIM=icarus        same, backed by Icarus Verilog instead
#   make sim  SIM=questa        same testbench, on a local licensed Questa/ModelSim install
#   make synth DESIGN=counter   open logical synthesis for Cyclone V with Yosys
#   make synth-all              synthesize the maintained smoke and library tops
#   make designs                list supported reference blocks
#   make library-test           pure Python tests for all reference blocks
#   make library-regression     required Verilator + Icarus library matrix
#   make netlist-all            pre-mapping schematics for all reference blocks
#   make netlist DESIGN=x       readable RTL schematic (results/netlist-*.svg)
#   make test                   run the whole PyTest suite with JUnit output
#   make report                 render XML/JSON evidence as readable Markdown
#   make dashboard              render a static cohort dashboard from GitHub data
#   make smoke                  the <5 min subset run on every push
#   make lint                   Verible lint over SystemVerilog sources
#   make gate REPORT=...        validate a local Quartus artifact against HEAD
#   make quartus-project        generate a Quartus project for TARGET_BOARD
#   make fpga-preflight         inspect native Quartus and JTAG without programming
#   make fpga-plan              print an isolated station build plan
#   make fpga-compile           compile committed inputs; never program the board
#   make clean

SHELL := /bin/bash
PYTHON ?= python3

# --- Parameters ---------------------------------------------------------------

DESIGN      ?= counter
TOPLEVEL    ?= $(DESIGN)
SIM_TOPLEVEL = $(if $(and $(filter skid_buffer,$(DESIGN)),$(filter skid_buffer,$(TOPLEVEL))),skid_buffer_sim_top,$(TOPLEVEL))
MODULE      ?= tb_$(DESIGN)
SIM         ?= verilator
VARIANT     = $(if $(filter edge_detector,$(DESIGN)),-r$(RISING))
SIM_BUILD   ?= sim_build/$(SIM)-$(DESIGN)-w$(WIDTH)$(VARIANT)
TOPLEVEL_LANG ?= verilog
TB_SEED     ?= 1234
TB_CYCLES   ?= 500
WIDTH       ?= $(if $(filter sync_2ff edge_detector,$(DESIGN)),1,8)
RISING      ?= 1
LIBRARY_DESIGNS := counter skid_buffer alu sync_2ff edge_detector control_fsm enabled_register bin2bcd bcd_to_7seg
LIBRARY_TEST_DIRS := $(addprefix tests/,$(LIBRARY_DESIGNS))
TARGET_BOARD ?= de1_soc
QUARTUS_PROJECT ?= $(DESIGN)
QUARTUS_REVISION ?= $(QUARTUS_PROJECT)
# The generated project targets the board wrapper (de1_soc_top / de10_standard_top)
# so it reaches the physical pins, matching the committed .qsf. Override with
# QUARTUS_TOP=... to synthesize a bare design without a board wrapper.
QUARTUS_TOP ?= $(TARGET_BOARD)_top

TEMPLATE_ROOT := $(CURDIR)
RESULTS_DIR   ?= $(TEMPLATE_ROOT)/results
COCOTB_RESULTS_FILE ?= $(RESULTS_DIR)/cocotb-$(SIM)-$(DESIGN)-w$(WIDTH)$(VARIANT).xml
SIM_REPORT    ?= $(RESULTS_DIR)/report-sim-$(SIM)-$(DESIGN)-w$(WIDTH)$(VARIANT).md
SMOKE_REPORT  ?= $(RESULTS_DIR)/report-smoke.md
TEST_REPORT   ?= $(RESULTS_DIR)/report-test.md
SYNTH_ALL_REPORT ?= $(RESULTS_DIR)/report-synth-all.md
SRC_DIRS      ?= src lib
REPO          ?=
ORG           ?=
DASHBOARD_OUTPUT ?= $(RESULTS_DIR)/dashboard.md
DASHBOARD_REPO_PATH ?= .
DASHBOARD_REPOS_ROOT ?= .
DASHBOARD_CONFIG ?=
QUARTUS_BIN ?=
STATION_TIMEOUT ?= 1800
STATION_PROFILE ?= panel

# Lint all sources, but isolate known library tops from station/vendor RTL.
ALL_VERILOG_SOURCES := $(sort $(wildcard $(addsuffix /*.sv,$(SRC_DIRS))) \
                          $(wildcard $(addsuffix /**/*.sv,$(SRC_DIRS))))
VERILOG_SOURCES ?= $(if $(filter $(DESIGN),$(LIBRARY_DESIGNS)),$(if $(filter counter,$(DESIGN)),src/counter.sv,lib/$(DESIGN)/$(DESIGN).sv),$(ALL_VERILOG_SOURCES))
SIM_VERILOG_SOURCES = $(VERILOG_SOURCES) $(wildcard tests/$(DESIGN)/*.sv)

# Where the CocoTB testbench and its model/checker live.
export PYTHONPATH := $(TEMPLATE_ROOT)/tests/$(DESIGN):$(TEMPLATE_ROOT)/tests/common:$(TEMPLATE_ROOT)/tests:$(TEMPLATE_ROOT)/tools:$(PYTHONPATH)
export TB_SEED
export TB_CYCLES
export RISING

EXTRA_ARGS += $(if $(filter verilator,$(SIM)),--trace --trace-structs)
COCOTB_HDL_TIMEUNIT      ?= 1ns
COCOTB_HDL_TIMEPRECISION ?= 1ps

.PHONY: all sim synth synth-all netlist netlist-all show designs library-test library-regression test report dashboard smoke lint gate quartus-project fpga-preflight fpga-plan fpga-compile clean help

all: test

help:
	@grep -E '^#   make' $(MAKEFILE_LIST) | sed 's/^#   /  /'

designs:
	@printf '%s\n' $(LIBRARY_DESIGNS)
	@echo "WIDTH>=1: counter/skid_buffer/alu; sync_2ff: single bit; edge_detector: RISING=0|1"
	@echo "control_fsm: start/finish; enabled_register: WIDTH>=1; bin2bcd: WIDTH=1..64; bcd_to_7seg: active_low input"

# --- Simulation ---------------------------------------------------------------
# Delegates to cocotb's own makefiles so the simulator invocation stays the
# upstream-supported one rather than a hand-rolled command line.

sim:
	@rm -f "$(COCOTB_RESULTS_FILE)" "$(SIM_REPORT)"; \
	$(MAKE) -f $(TEMPLATE_ROOT)/sim.mk check-sim-prereqs \
		DESIGN=$(DESIGN) TOPLEVEL=$(SIM_TOPLEVEL) MODULE=$(MODULE) SIM=$(SIM) WIDTH=$(WIDTH) \
		|| { status=$$?; exit $$status; }; \
	status=0; $(MAKE) -f $(TEMPLATE_ROOT)/sim.mk sim \
		DESIGN=$(DESIGN) TOPLEVEL=$(SIM_TOPLEVEL) MODULE=$(MODULE) SIM=$(SIM) \
		WIDTH=$(WIDTH) \
		SIM_BUILD="$(SIM_BUILD)" \
		$(if $(COCOTB_RESULTS_FILE),COCOTB_RESULTS_FILE="$(COCOTB_RESULTS_FILE)") \
		VERILOG_SOURCES="$(SIM_VERILOG_SOURCES)" || status=$$?; \
	if [[ $$status -ne 0 && "$(SIM)" == "questa" ]]; then \
		echo "ERROR: CocoTB launched the native Siemens flow for SIM=questa, but the run failed."; \
		echo "       On a lab machine this usually means the shell lacks the local Questa/ModelSim setup or the license is unavailable."; \
		echo "       SIM=questa is local-only; the DevContainer, GitHub Actions and Kabré must stay on SIM=verilator or SIM=icarus."; \
	fi; \
	if [[ -s "$(COCOTB_RESULTS_FILE)" ]]; then \
		python3 tools/human_report.py --junit "$(COCOTB_RESULTS_FILE)" \
			--primary-junit "$(COCOTB_RESULTS_FILE)" --output "$(SIM_REPORT)" --require-pass \
			|| { report_status=$$?; [[ $$status -ne 0 ]] || status=$$report_status; }; \
	else \
		echo "ERROR: simulator did not produce nonempty evidence at $(COCOTB_RESULTS_FILE)." >&2; \
		[[ $$status -ne 0 ]] || status=2; \
	fi; \
	exit $$status

# --- Open logical synthesis --------------------------------------------------
#
# Yosys 0.40 documents that Cyclone V must use synth_intel_alm rather than the
# older synth_intel backend. This gate proves elaboration and technology
# mapping to Cyclone V ALM cells. It does not perform placement, routing,
# bitstream generation or timing analysis. Quartus remains authoritative for
# those device-specific stages. TARGET_BOARD therefore affects Quartus and
# hardware jobs, but not this open logical synthesis gate.

SYNTH_JSON ?= $(RESULTS_DIR)/yosys-$(DESIGN)-w$(WIDTH)$(VARIANT).json
SYNTH_PARAMS = $(if $(filter sync_2ff control_fsm bcd_to_7seg,$(DESIGN)),,$(if $(filter edge_detector,$(DESIGN)),chparam -set RISING $(RISING) $(TOPLEVEL);,chparam -set WIDTH $(WIDTH) $(TOPLEVEL);))
SYNTH_ALL_JSON := $(RESULTS_DIR)/yosys-counter-w8.json \
	$(RESULTS_DIR)/yosys-alu-w8.json \
	$(RESULTS_DIR)/yosys-alu-w32.json \
	$(RESULTS_DIR)/yosys-skid_buffer-w1.json \
	$(RESULTS_DIR)/yosys-skid_buffer-w8.json \
	$(RESULTS_DIR)/yosys-skid_buffer-w32.json \
	$(RESULTS_DIR)/yosys-sync_2ff-w1.json \
	$(RESULTS_DIR)/yosys-edge_detector-w1-r1.json \
	$(RESULTS_DIR)/yosys-edge_detector-w1-r0.json \
	$(RESULTS_DIR)/yosys-control_fsm-w8.json \
	$(RESULTS_DIR)/yosys-enabled_register-w1.json \
	$(RESULTS_DIR)/yosys-enabled_register-w8.json \
	$(RESULTS_DIR)/yosys-enabled_register-w32.json \
	$(RESULTS_DIR)/yosys-bin2bcd-w8.json \
	$(RESULTS_DIR)/yosys-bin2bcd-w32.json \
	$(RESULTS_DIR)/yosys-bcd_to_7seg-w8.json

synth: | $(RESULTS_DIR)
	yosys -p "read_verilog -sv $(VERILOG_SOURCES); \
		$(SYNTH_PARAMS) \
		synth_intel_alm -family cyclonev -top $(TOPLEVEL); \
		check -assert; write_json $(SYNTH_JSON)"
	python3 tools/check_yosys_netlist.py $(SYNTH_JSON) --top $(TOPLEVEL)

synth-all:
	@rm -f $(SYNTH_ALL_JSON) "$(SYNTH_ALL_REPORT)"; \
	status=0; \
	$(MAKE) synth DESIGN=counter TOPLEVEL=counter WIDTH=8 || status=$$?; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=alu TOPLEVEL=alu WIDTH=8 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=alu TOPLEVEL=alu WIDTH=32 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=skid_buffer TOPLEVEL=skid_buffer WIDTH=1 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=skid_buffer TOPLEVEL=skid_buffer WIDTH=8 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=skid_buffer TOPLEVEL=skid_buffer WIDTH=32 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=sync_2ff TOPLEVEL=sync_2ff WIDTH=1 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=edge_detector TOPLEVEL=edge_detector WIDTH=1 RISING=1 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=edge_detector TOPLEVEL=edge_detector WIDTH=1 RISING=0 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=control_fsm TOPLEVEL=control_fsm WIDTH=8 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=enabled_register TOPLEVEL=enabled_register WIDTH=1 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=enabled_register TOPLEVEL=enabled_register WIDTH=8 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=enabled_register TOPLEVEL=enabled_register WIDTH=32 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=bin2bcd TOPLEVEL=bin2bcd WIDTH=8 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=bin2bcd TOPLEVEL=bin2bcd WIDTH=32 || status=$$?; fi; \
	if [[ $$status -eq 0 ]]; then $(MAKE) synth DESIGN=bcd_to_7seg TOPLEVEL=bcd_to_7seg WIDTH=8 || status=$$?; fi; \
	python3 tools/human_report.py \
		$(foreach path,$(SYNTH_ALL_JSON),--synthesis "$(path)") \
		--output "$(SYNTH_ALL_REPORT)" --require-pass \
		|| { report_status=$$?; [[ $$status -ne 0 ]] || status=$$report_status; }; \
	exit $$status

# --- Readable RTL schematic (pedagogical view) -------------------------------
# Complementary to `synth`: renders the *elaborated* logic (pre technology
# mapping) so a student can SEE their RTL. NOT a P&R/bitstream/STA result;
# Quartus remains authoritative. Needs yosys + graphviz 'dot' (DevContainer).
NETLIST_DOT ?= $(RESULTS_DIR)/netlist-$(DESIGN)-w$(WIDTH).dot
NETLIST_SVG ?= $(RESULTS_DIR)/netlist-$(DESIGN)-w$(WIDTH).svg

netlist show: | $(RESULTS_DIR)
	python3 tools/yosys_show.py \
		--design $(DESIGN) --top $(TOPLEVEL) --width $(WIDTH) \
		$(if $(filter sync_2ff edge_detector control_fsm bcd_to_7seg,$(DESIGN)),--no-width) \
		$(if $(filter edge_detector,$(DESIGN)),--rising $(RISING)) \
		--results-dir $(RESULTS_DIR) \
		--sources $(VERILOG_SOURCES)

netlist-all:
	@set -e; for design in counter skid_buffer alu enabled_register bin2bcd; do \
		$(MAKE) netlist DESIGN=$$design TOPLEVEL=$$design WIDTH=8; \
	done
	$(MAKE) netlist DESIGN=sync_2ff TOPLEVEL=sync_2ff WIDTH=1
	$(MAKE) netlist DESIGN=edge_detector TOPLEVEL=edge_detector WIDTH=1 RISING=1
	$(MAKE) netlist DESIGN=edge_detector TOPLEVEL=edge_detector WIDTH=1 RISING=0
	$(MAKE) netlist DESIGN=control_fsm TOPLEVEL=control_fsm WIDTH=8
	$(MAKE) netlist DESIGN=bcd_to_7seg TOPLEVEL=bcd_to_7seg WIDTH=8

# --- Test aggregation ---------------------------------------------------------

$(RESULTS_DIR):
	@mkdir -p $(RESULTS_DIR)

library-test: | $(RESULTS_DIR)
	$(PYTHON) -m pytest $(LIBRARY_TEST_DIRS) -m "not simulation" \
		--junitxml="$(RESULTS_DIR)/junit-library-unit.xml" \
		--html="$(RESULTS_DIR)/library-unit.html" --self-contained-html -v

# Unlike portable pytest (which skips unavailable simulators), this demo target
# requires both tools: a green regression must not mean that RTL was skipped.
library-regression: | $(RESULTS_DIR)
	@rm -f "$(RESULTS_DIR)/junit-library.xml" "$(RESULTS_DIR)/library.html"
	@command -v verilator >/dev/null || { echo "Missing Verilator; use the DevContainer"; exit 2; }
	@command -v iverilog >/dev/null || { echo "Missing Icarus; use the DevContainer"; exit 2; }
	$(PYTHON) -m pytest $(LIBRARY_TEST_DIRS) \
		--junitxml="$(RESULTS_DIR)/junit-library.xml" \
		--html="$(RESULTS_DIR)/library.html" --self-contained-html -v

test: | $(RESULTS_DIR)
	@rm -f "$(RESULTS_DIR)/junit.xml" "$(TEST_REPORT)"; \
	status=0; python3 -m pytest tests/ \
		--junitxml=$(RESULTS_DIR)/junit.xml \
		--html=$(RESULTS_DIR)/report.html --self-contained-html \
		-v || status=$$?; \
	python3 tools/human_report.py --junit "$(RESULTS_DIR)/junit.xml" \
		--primary-junit "$(RESULTS_DIR)/junit.xml" --output "$(TEST_REPORT)" \
		|| { report_status=$$?; [[ $$status -ne 0 ]] || status=$$report_status; }; \
	exit $$status

report: | $(RESULTS_DIR)
	python3 tools/human_report.py \
		--results-dir $(RESULTS_DIR) \
		--output $(RESULTS_DIR)/report.md \
		--discover \
		$(if $(PRIMARY_JUNIT),--primary-junit $(PRIMARY_JUNIT))

dashboard: | $(RESULTS_DIR)
	@if [[ -n "$(DASHBOARD_CONFIG)" ]]; then \
		$(PYTHON) tools/dashboard.py \
			--config "$(DASHBOARD_CONFIG)" \
			$(if $(ORG),--owner "$(ORG)") \
			$(if $(REPO),--repo "$(REPO)") \
			--output "$(DASHBOARD_OUTPUT)"; \
	elif [[ -n "$(REPO)" && -z "$(ORG)" ]]; then \
		$(PYTHON) tools/dashboard.py \
			--repo "$(REPO)" \
			--output "$(DASHBOARD_OUTPUT)"; \
	else \
		echo "Set DASHBOARD_CONFIG=config/cohort.json (and ORG for one installation), or REPO=owner/name." >&2; \
		exit 2; \
	fi

smoke: | $(RESULTS_DIR)
	@rm -f "$(RESULTS_DIR)/junit-smoke.xml" "$(SMOKE_REPORT)"; \
	status=0; python3 -m pytest tests/ -m "not simulation and not hardware" \
		--junitxml=$(RESULTS_DIR)/junit-smoke.xml \
		-q || status=$$?; \
	python3 tools/human_report.py --junit "$(RESULTS_DIR)/junit-smoke.xml" \
		--primary-junit "$(RESULTS_DIR)/junit-smoke.xml" --output "$(SMOKE_REPORT)" \
		|| { report_status=$$?; [[ $$status -ne 0 ]] || status=$$report_status; }; \
	exit $$status

# --- Lint ---------------------------------------------------------------------
# Conditional tool with a Fase 0 time box. Non-blocking by design: it reports
# but does not fail the build, so a style opinion never blocks a correct design.

lint:
	@if command -v verible-verilog-lint >/dev/null 2>&1; then \
		verible-verilog-lint --rules_config_search $(ALL_VERILOG_SOURCES) || true; \
	else \
		echo "verible-verilog-lint not found; skipping lint"; \
	fi

format:
	@if command -v verible-verilog-format >/dev/null 2>&1; then \
		verible-verilog-format --inplace $(ALL_VERILOG_SOURCES); \
	else \
		echo "verible-verilog-format not found; skipping"; \
	fi

# --- Synthesis artifact gate --------------------------------------------------

REPORT ?= output_files/$(DESIGN).flow.rpt

gate:
	python3 tools/artifact_gate.py \
		--repo . \
		--report $(REPORT) \
		--commit $${GITHUB_SHA:-HEAD} \
		--source-glob src --source-glob lib --source-glob quartus

quartus-project:
	@if [[ -n "$(USE_COMMITTED_QSF)" ]]; then \
		echo "USE_COMMITTED_QSF: copying the committed revision-per-board project into place"; \
		cp quartus/digital_design_template.qpf .; \
		cp quartus/de1_soc.qsf quartus/de10_standard.qsf .; \
		echo "copied .qpf + .qsf to the template root (the Quartus project directory)"; \
		echo "open digital_design_template.qpf in Quartus, or set the revision with TARGET_BOARD"; \
	elif command -v quartus_sh >/dev/null 2>&1; then \
		quartus_sh -t quartus/build_project.tcl \
			--target-board "$(TARGET_BOARD)" \
			--project-name "$(QUARTUS_PROJECT)" \
			--revision "$(QUARTUS_REVISION)" \
			--top-level "$(QUARTUS_TOP)"; \
	else \
		echo "quartus_sh not found; install Quartus or run this target on the lab station"; \
		echo "(or use USE_COMMITTED_QSF=1 to place the committed project without Quartus)"; \
		exit 1; \
	fi

# --- Native FPGA station ------------------------------------------------------

fpga-preflight:
	$(PYTHON) tools/fpga_station.py preflight \
		$(if $(QUARTUS_BIN),--quartus-bin "$(QUARTUS_BIN)") \
		--board "$(TARGET_BOARD)" --profile "$(STATION_PROFILE)"

fpga-plan:
	$(PYTHON) tools/fpga_station.py plan \
		$(if $(QUARTUS_BIN),--quartus-bin "$(QUARTUS_BIN)") \
		--board "$(TARGET_BOARD)" --profile "$(STATION_PROFILE)"

fpga-compile:
	$(PYTHON) tools/fpga_station.py compile \
		$(if $(QUARTUS_BIN),--quartus-bin "$(QUARTUS_BIN)") \
		--board "$(TARGET_BOARD)" --profile "$(STATION_PROFILE)" --timeout "$(STATION_TIMEOUT)"

# --- Housekeeping -------------------------------------------------------------

clean:
	rm -rf sim_build results __pycache__ .pytest_cache dump.vcd dump.fst
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
