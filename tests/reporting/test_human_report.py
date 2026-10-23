# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Tests for the dependency-free human-readable report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from human_report import main, render_report  # noqa: E402


@pytest.mark.parametrize(("xml", "expected"), [
    ('<testsuite><testcase name="pass"/></testsuite>', 0),
    ('<testsuite><testcase><failure/></testcase></testsuite>', 1),
    ('<testsuite><testcase><error/></testcase></testsuite>', 1),
    ('<testsuite><testcase><skipped/></testcase></testsuite>', 1),
    ('<testsuite/>', 1),
    ('not XML', 1),
])
def test_cli_requires_actual_passes_when_used_as_a_gate(tmp_path, xml, expected):
    source = tmp_path / "cocotb.xml"
    source.write_text(xml)
    output = tmp_path / "report.md"
    assert main(["--junit", str(source), "--output", str(output), "--require-pass"]) == expected
    assert output.is_file()


def test_presentation_only_mode_preserves_failure_report_without_gating(tmp_path):
    source = tmp_path / "failed.xml"
    source.write_text("<testsuite><testcase><failure/></testcase></testsuite>")
    output = tmp_path / "report.md"
    assert main(["--junit", str(source), "--output", str(output)]) == 0
    assert "`FAIL`" in output.read_text(encoding="utf-8")


def test_cli_gate_rejects_missing_evidence(tmp_path):
    assert main(["--junit", str(tmp_path / "missing.xml"),
                 "--output", str(tmp_path / "report.md"), "--require-pass"]) == 1


def test_report_aggregates_junit_simulation_synthesis_and_slurm(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "cocotb-icarus-skid-buffer-width-32.xml"
    junit.write_text(
        """
<testsuite tests="3">
  <testcase name="passes" time="0.2"/>
  <testcase name="skips"><skipped/></testcase>
  <testcase name="fails"><failure message="mismatch"/></testcase>
</testsuite>
""".strip(),
        encoding="utf-8",
    )
    synthesis = tmp_path / "yosys-skid_buffer-w32.json"
    synthesis.write_text(
        json.dumps(
            {
                "modules": {
                    "skid_buffer": {
                        "cells": {
                            "a": {"type": "MISTRAL_ALUT6"},
                            "b": {"type": "MISTRAL_FF"},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    report = render_report(
        [junit],
        [synthesis],
        {"Job": "608383", "Partición": "kura-wide"},
    )

    assert "**Veredicto agregado:** `FAIL`" in report
    assert "| 3 | 1 | 1 | 0 | 1 |" in report
    assert "| icarus | skid-buffer | 32 | 3 | `FAIL` |" in report
    assert "| skid_buffer | 32 | 2 | 2 | `PASS` |" in report
    assert "| Job | `608383` |" in report


def test_report_is_explicit_when_evidence_is_missing() -> None:
    report = render_report([], [])
    assert "**Veredicto agregado:** `FAIL`" in report
    assert "_No aplica a esta ejecución_" in report
    assert "Ejecutar `make synth-all`" in report


def test_synthesis_only_report_can_pass(tmp_path: Path) -> None:
    synthesis = tmp_path / "yosys-counter-w8.json"
    synthesis.write_text(
        json.dumps(
            {
                "modules": {
                    "counter": {
                        "cells": {"counter_ff": {"type": "MISTRAL_FF"}}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    report = render_report([], [synthesis])

    assert "**Veredicto agregado:** `PASS`" in report
    assert "| counter | 8 | 1 | 1 | `PASS` |" in report


def test_missing_requested_evidence_fails_instead_of_using_stale_files(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "junit.xml"
    stale.write_text(
        '<testsuite><testcase name="old-pass"/></testsuite>',
        encoding="utf-8",
    )

    report = render_report([tmp_path / "junit-smoke.xml"], [])

    assert "**Veredicto agregado:** `FAIL`" in report
    assert "junit-smoke.xml" in report
    assert "junit.xml" not in report


def test_top_level_junit_prevents_double_counting_cocotb(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    cocotb = tmp_path / "cocotb-verilator-counter-w8.xml"
    junit.write_text(
        '<testsuite><testcase name="simulation"/></testsuite>',
        encoding="utf-8",
    )
    cocotb.write_text(
        '<testsuite><testcase name="rtl_case"/></testsuite>',
        encoding="utf-8",
    )

    report = render_report([junit, cocotb], [])

    assert "| 1 | 1 | 0 | 0 | 0 |" in report
    assert "| verilator | counter | 8 | 1 | `PASS` |" in report


def test_explicit_primary_junit_wins_over_stale_full_suite(
    tmp_path: Path,
) -> None:
    full = tmp_path / "junit.xml"
    smoke = tmp_path / "junit-smoke.xml"
    full.write_text(
        "<testsuite>" + '<testcase name="old"/>' * 10 + "</testsuite>",
        encoding="utf-8",
    )
    smoke.write_text(
        "<testsuite>" + '<testcase name="current"/>' * 2 + "</testsuite>",
        encoding="utf-8",
    )

    report = render_report([full, smoke], [], primary_junit=smoke)

    assert "| 2 | 2 | 0 | 0 | 0 |" in report
