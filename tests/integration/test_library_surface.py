# SPDX-License-Identifier: Apache-2.0
"""Library discovery and OpenCE contracts; no simulator needed."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Monorepo keeps datasheets beside template/; the standalone export at the root.
DOCS = next((path for path in (ROOT / "docs" / "modules", ROOT.parent / "docs" / "modules")
             if path.is_dir()), ROOT / "docs" / "modules")
DESIGNS = ("counter", "skid_buffer", "alu", "sync_2ff", "edge_detector",
           "control_fsm", "enabled_register", "bin2bcd", "bcd_to_7seg")
SECTIONS = (
    "Revisions", "Document Conventions", "Introduction", "Block Diagram",
    "Configuration", "Protocols", "Memory Map", "Registers", "Clock Domains",
    "Reset Domains", "Interrupts", "Arbitration", "Debugging", "Synthesis",
    "Verification", "Microarchitecture", "References",
)

sys.path.insert(0, str(ROOT / "tools"))
from human_report import render_report  # noqa: E402


@pytest.mark.parametrize("rising", [0, 1])
def test_polarity_artifacts_keep_top_and_variant_in_report(tmp_path, rising) -> None:
    netlist = tmp_path / f"yosys-edge_detector-w1-r{rising}.json"
    netlist.write_text(json.dumps({"modules": {
        "edge_detector": {"cells": {"q": {"type": "MISTRAL_FF"}}},
        "MISTRAL_FF": {"cells": {}},
    }}), encoding="utf-8")
    junit = tmp_path / f"cocotb-verilator-edge_detector-w1-r{rising}.xml"
    junit.write_text("<testsuite><testcase/></testsuite>", encoding="utf-8")
    report = render_report([junit], [netlist])
    assert "**Veredicto agregado:** `PASS`" in report
    assert f"| edge_detector (RISING={rising}) | 1 | 1 | 1 | `PASS` |" in report
    assert f"| verilator | edge_detector (RISING={rising}) | 1 | 1 | `PASS` |" in report


@pytest.mark.parametrize("design", DESIGNS)
def test_every_library_block_has_source_and_driver(design: str) -> None:
    source = (ROOT / "src/counter.sv" if design == "counter"
              else ROOT / "lib" / design / f"{design}.sv")
    assert source.is_file()
    assert (ROOT / "tests" / design / f"tb_{design}.py").is_file()


@pytest.mark.parametrize("design", DESIGNS)
def test_opence_sections_and_microarchitecture_sketch(design: str) -> None:
    if not DOCS.is_dir():
        pytest.skip("module datasheets are absent from this payload")
    text = (DOCS / f"{design}.md").read_text(encoding="utf-8")
    for section in SECTIONS:
        assert f"## {section}\n" in text, (design, section)
    assert "```mermaid\nflowchart LR" in text
    assert "No aplica" in text


@pytest.mark.parametrize("design", DESIGNS)
def test_synthesis_and_netlist_select_bare_library_top(design: str) -> None:
    if not shutil.which("make"):
        pytest.skip("GNU Make is absent")
    for target in ("synth", "netlist"):
        proc = subprocess.run(
            ["make", "-n", target, f"DESIGN={design}"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "skid_buffer_sim_top" not in proc.stdout
        assert "jtag_counter.sv" not in proc.stdout
        assert "wr_decoder.sv" not in proc.stdout
        assert ("src/counter.sv" if design == "counter"
                else f"lib/{design}/{design}.sv") in proc.stdout
        if design in ("sync_2ff", "control_fsm", "bcd_to_7seg"):
            assert "chparam" not in proc.stdout
            if target == "netlist":
                assert "--no-width" in proc.stdout
        if design == "edge_detector" and target == "synth":
            assert "chparam -set RISING 1 edge_detector" in proc.stdout
            assert "chparam -set WIDTH" not in proc.stdout


def test_discovery_includes_required_families_and_legacy_blocks() -> None:
    proc = subprocess.run(["make", "-s", "designs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    assert set(DESIGNS) <= set(proc.stdout.splitlines())


@pytest.mark.parametrize("design", ["enabled_register", "bin2bcd"])
def test_new_width_parameter_reaches_synthesis_and_netlist(design: str) -> None:
    for target in ("synth", "netlist"):
        proc = subprocess.run(
            ["make", "-n", target, f"DESIGN={design}", "WIDTH=32"],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert proc.returncode == 0, proc.stderr
        expected = (f"chparam -set WIDTH 32 {design}" if target == "synth"
                    else "--width 32")
        assert expected in proc.stdout


@pytest.mark.parametrize("target", ["synth-all", "netlist-all"])
def test_aggregates_cover_every_library_design(target: str) -> None:
    # GNU Make executes recursive recipe lines even under -n. In synth-all
    # that would also remove artifacts/run the reporter: inspect, do not run.
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split(f"\n{target}:\n", 1)[1].split("\n# ---", 1)[0]
    for design in DESIGNS:
        explicit = f"DESIGN={design}" in recipe
        loop = any(line.strip().startswith("@set -e; for design in ")
                   and design in line.split(" in ", 1)[1].split(";")[0].split()
                   for line in recipe.splitlines())
        assert explicit or loop, (target, design)
