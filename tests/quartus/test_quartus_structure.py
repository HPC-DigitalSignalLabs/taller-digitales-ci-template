# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Structure tests for the committed Quartus project.

These run with NO Quartus installed: they are pure text/AST assertions over the
committed ``.qpf``/``.qsf``/board-top files, so they belong in ``make smoke`` and
in CI. They guard the "open in Quartus and compile, no reconstruction" promise
and, critically, protect the CI artifact gate from a regression where
``output_files/.gitignore`` starts ignoring the tracked flow report or its
provenance sidecar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE_ROOT = Path(__file__).resolve().parents[2]
QUARTUS = TEMPLATE_ROOT / "quartus"
BOARDS = QUARTUS / "boards"

# Physical top-level ports every board wrapper must expose.
EXPECTED_PORTS = ["CLOCK_50", "KEY", "SW", "LEDR", "HEX0", "HEX5"]

BOARD_TOPS = {
    "de1_soc_top": BOARDS / "de1_soc_top.sv",
    "de10_standard_top": BOARDS / "de10_standard_top.sv",
}

# revision -> (qsf path, device, top-level entity, sdc filename)
QSF_SPECS = {
    "de1_soc": (QUARTUS / "de1_soc.qsf", "5CSEMA5F31C6", "de1_soc_top", "de1_soc.sdc"),
    "de10_standard": (
        QUARTUS / "de10_standard.qsf",
        "5CSXFC6D6F31C6",
        "de10_standard_top",
        "de10_standard.sdc",
    ),
}


def _read(path: Path) -> str:
    assert path.is_file(), f"missing expected file: {path}"
    return path.read_text(encoding="utf-8")


# --- Board tops ---------------------------------------------------------------


@pytest.mark.parametrize("entity, path", BOARD_TOPS.items())
def test_board_top_exists_and_declares_ports(entity: str, path: Path) -> None:
    text = _read(path)
    assert re.search(rf"\bmodule\s+{re.escape(entity)}\b", text), (
        f"{path} does not declare module {entity}"
    )
    for port in EXPECTED_PORTS:
        assert re.search(rf"\b{re.escape(port)}\b", text), (
            f"{path} does not declare top-level port {port}"
        )


def test_shared_seg7_decoder_exists() -> None:
    text = _read(BOARDS / "seg7_hex.sv")
    assert re.search(r"\bmodule\s+seg7_hex\b", text)


# --- QSF files ----------------------------------------------------------------


@pytest.mark.parametrize("revision, spec", QSF_SPECS.items())
def test_qsf_core_assignments(revision: str, spec: tuple) -> None:
    qsf_path, device, top, sdc_name = spec
    text = _read(qsf_path)

    assert re.search(rf"-name\s+DEVICE\s+{re.escape(device)}\b", text), (
        f"{qsf_path} does not set DEVICE {device}"
    )
    assert re.search(rf"-name\s+TOP_LEVEL_ENTITY\s+{re.escape(top)}\b", text), (
        f"{qsf_path} does not set TOP_LEVEL_ENTITY {top}"
    )
    assert re.search(r'-name\s+FAMILY\s+"Cyclone V"', text), (
        f"{qsf_path} does not set FAMILY \"Cyclone V\""
    )
    assert re.search(r"-name\s+PROJECT_OUTPUT_DIRECTORY\s+output_files\b", text), (
        f"{qsf_path} does not set PROJECT_OUTPUT_DIRECTORY output_files"
    )
    assert re.search(rf"-name\s+SDC_FILE\s+\S*{re.escape(sdc_name)}\b", text), (
        f"{qsf_path} does not reference its SDC {sdc_name}"
    )
    assert "quartus_sh:quartus/post_flow.tcl" in text, (
        f"{qsf_path} does not register quartus/post_flow.tcl as POST_FLOW_SCRIPT_FILE"
    )
    # The SDC referenced must be the one that matches the revision.
    other_sdc = [n for r, (_p, _d, _t, n) in QSF_SPECS.items() if r != revision][0]
    assert other_sdc not in text, (
        f"{qsf_path} references the wrong board SDC ({other_sdc})"
    )


def test_de10_qsf_marked_unverified() -> None:
    text = _read(QUARTUS / "de10_standard.qsf")
    assert "UNVERIFIED" in text, "de10_standard.qsf must carry the UNVERIFIED (BL-011) marker"
    assert "BL-011" in text
    # And it must NOT smuggle in confident DE1-SoC pin locations.
    assert "PIN_AF14" not in text, (
        "de10_standard.qsf must not copy DE1-SoC pin locations as if verified"
    )


def test_de10_top_marked_unverified() -> None:
    text = _read(BOARDS / "de10_standard_top.sv")
    assert "UNVERIFIED" in text
    assert "BL-011" in text


# --- QPF file -----------------------------------------------------------------


def test_qpf_lists_both_revisions() -> None:
    text = _read(QUARTUS / "digital_design_template.qpf")
    for revision in QSF_SPECS:
        assert re.search(rf'PROJECT_REVISION\s*=\s*"{re.escape(revision)}"', text), (
            f"digital_design_template.qpf does not declare revision {revision}"
        )


# --- output_files/.gitignore: artifact-gate regression guard ------------------


def test_output_files_gitignore_keeps_tracked_evidence() -> None:
    gitignore = TEMPLATE_ROOT / "output_files" / ".gitignore"
    text = _read(gitignore)
    lines = [ln.strip() for ln in text.splitlines()]

    # No rule may ignore the flow report or the provenance sidecar. An ignore
    # rule is any non-comment, non-negation line matching those suffixes.
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("!"):
            continue  # negation re-includes; that is what we want
        assert not ln.endswith(".flow.rpt"), f"gitignore ignores the flow report: {ln!r}"
        assert "provenance.json" not in ln, (
            f"gitignore ignores the provenance sidecar: {ln!r}"
        )
        assert ln not in ("*.rpt", "*.json"), (
            f"gitignore rule {ln!r} would swallow the tracked evidence"
        )

    # And the deliberate negations must be present.
    assert any(ln.startswith("!") and ln.endswith(".flow.rpt") for ln in lines), (
        "output_files/.gitignore must re-include *.flow.rpt"
    )
    assert any(ln.startswith("!") and "provenance.json" in ln for ln in lines), (
        "output_files/.gitignore must re-include the provenance sidecar"
    )


def test_output_files_dir_is_kept() -> None:
    assert (TEMPLATE_ROOT / "output_files" / ".gitkeep").is_file()
