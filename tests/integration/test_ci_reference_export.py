# SPDX-License-Identifier: Apache-2.0
"""The hosted workflow must publish the references required by physical replay."""

from pathlib import Path

import pytest


def test_gatekeeper_exports_jtag_references_from_the_simulation_container():
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/gatekeeper.yml"
    if not workflow.is_file():
        pytest.skip("External workflow contract requires the complete repository checkout.")
    text = workflow.read_text(encoding="utf-8")
    suite = text.split("- name: Run the suite in the image", 1)[1].split("- name:", 1)[0]
    assert "-e JTAG_EXPORT_DIR=results" in suite
    assert "make test" in suite
    for simulator in ("verilator", "icarus"):
        assert f"test -s results/jtag-counter-{simulator}.json" in text
        assert f"test -s results/cocotb-{simulator}-jtag_counter-w8.xml" in text
    assert "output_files/*.rbf" not in text
