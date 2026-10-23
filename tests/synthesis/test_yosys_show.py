# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Tests for the readable RTL schematic tool.

These are pure-Python tests: they never invoke ``yosys`` or Graphviz ``dot``,
so they pass on the developer's host where neither tool is installed. Only the
tool's pure functions (script/command construction, path naming and tool
detection) are exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import yosys_show  # noqa: E402
from yosys_show import (  # noqa: E402
    build_dot_command,
    build_yosys_command,
    build_yosys_script,
    dot_output_path,
    netlist_stem,
    render_schematic,
    svg_output_path,
)


def test_script_contains_readability_stages_width_and_top() -> None:
    sources = [Path("lib/skid_buffer.sv"), Path("src/top.sv")]
    dot_path = Path("results/netlist-skid_buffer-w8.dot")
    script = build_yosys_script(sources, "skid_buffer", 8, dot_path)

    # Elaboration/readability passes, not technology mapping.
    for stage in ("read_verilog -sv", "hierarchy -top skid_buffer", "proc", "opt", "memory -nomap", "opt_clean"):
        assert stage in script
    # WIDTH parameterisation is applied to the requested top.
    assert "chparam -set WIDTH 8 skid_buffer" in script
    # It stays a readable view: no Cyclone V technology mapping.
    assert "synth_intel_alm" not in script
    # show emits dot with the stem (extension stripped) and no title.
    assert "show -format dot" in script
    assert "-notitle" in script
    # prefix carries the artifact stem without the .dot extension (path
    # separator is OS-dependent, so match on the stem only).
    assert "netlist-skid_buffer-w8" in script
    assert ".dot -notitle" not in script  # prefix must not carry the extension
    # every source is present (match on filename, separator is OS-dependent)
    assert "skid_buffer.sv" in script
    assert "top.sv" in script


def test_yosys_command_wraps_script() -> None:
    command = build_yosys_command("read_verilog foo.sv")
    assert command == ["yosys", "-p", "read_verilog foo.sv"]


def test_single_bit_synchronizer_has_no_width_override() -> None:
    script = build_yosys_script(
        [Path("sync_2ff.sv")], "sync_2ff", 1, Path("sync.dot"),
        apply_width=False,
    )
    assert "chparam" not in script
    assert "hierarchy -top sync_2ff" in script


@pytest.mark.parametrize("rising", [0, 1])
def test_edge_detector_polarity_is_elaborated(rising: int) -> None:
    script = build_yosys_script(
        [Path("edge_detector.sv")], "edge_detector", 1, Path("edge.dot"),
        apply_width=False, rising=rising,
    )
    assert "WIDTH" not in script
    assert f"chparam -set RISING {rising} edge_detector" in script


def test_polarities_have_distinct_rendered_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(yosys_show.shutil, "which", lambda tool: f"/bin/{tool}")
    paths = []
    for rising in (0, 1):
        paths.append(render_schematic(
            [Path("edge_detector.sv")], "edge_detector", 1, "edge_detector",
            tmp_path, runner=lambda *args, **kwargs: None,
            apply_width=False, rising=rising,
        ))
    assert paths[0][1].name == "netlist-edge_detector-w1-r0.svg"
    assert paths[1][1].name == "netlist-edge_detector-w1-r1.svg"


def test_dot_command_construction() -> None:
    dot_path = Path("results/netlist-counter-w8.dot")
    svg_path = Path("results/netlist-counter-w8.svg")
    assert build_dot_command(dot_path, svg_path) == [
        "dot",
        "-Tsvg",
        str(dot_path),
        "-o",
        str(svg_path),
    ]


def test_output_path_naming() -> None:
    results = Path("results")
    assert netlist_stem("counter", 8) == "netlist-counter-w8"
    assert dot_output_path(results, "counter", 8) == results / "netlist-counter-w8.dot"
    assert svg_output_path(results, "skid_buffer", 32) == results / "netlist-skid_buffer-w32.svg"


def test_missing_yosys_raises_actionable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(yosys_show.shutil, "which", lambda _tool: None)

    def _fail_runner(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("runner must not be invoked when yosys is missing")

    with pytest.raises(RuntimeError) as excinfo:
        render_schematic(
            sources=[tmp_path / "a.sv"],
            top="counter",
            width=8,
            design="counter",
            results_dir=tmp_path / "results",
            runner=_fail_runner,
        )
    message = str(excinfo.value)
    assert "yosys" in message
    assert "DevContainer" in message


def test_missing_dot_writes_dot_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # yosys present, dot absent: SVG is skipped, dot path still returned.
    monkeypatch.setattr(
        yosys_show.shutil,
        "which",
        lambda tool: "/usr/bin/yosys" if tool == "yosys" else None,
    )
    calls: list[list[str]] = []

    def _record_runner(command, check=False):  # noqa: ANN001
        calls.append(command)

    dot_path, svg_path = render_schematic(
        sources=[tmp_path / "a.sv"],
        top="counter",
        width=8,
        design="counter",
        results_dir=tmp_path / "results",
        runner=_record_runner,
    )
    assert svg_path is None
    assert dot_path == (tmp_path / "results" / "netlist-counter-w8.dot")
    assert len(calls) == 1  # only yosys was invoked, not dot
    assert calls[0][0] == "yosys"


def test_full_render_invokes_yosys_then_dot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        yosys_show.shutil,
        "which",
        lambda tool: f"/usr/bin/{tool}",
    )
    calls: list[list[str]] = []

    def _record_runner(command, check=False):  # noqa: ANN001
        calls.append(command)

    dot_path, svg_path = render_schematic(
        sources=[tmp_path / "a.sv"],
        top="counter",
        width=8,
        design="counter",
        results_dir=tmp_path / "results",
        runner=_record_runner,
    )
    assert dot_path == (tmp_path / "results" / "netlist-counter-w8.dot")
    assert svg_path == (tmp_path / "results" / "netlist-counter-w8.svg")
    assert [call[0] for call in calls] == ["yosys", "dot"]
