# SPDX-License-Identifier: Apache-2.0
"""Pure checker tests and required open-simulator coverage for the bridge."""

import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TEMPLATE / "tests"))
sys.path.insert(0, str(TEMPLATE / "tests" / "common"))
from jtag_counter.checker import check_log, check_simulation_suite, expected_log
from jtag_counter import checker
from jtag_counter.model import COUNT, RESET, STEP, STEPS, CounterRegisterModel
from transport import Transaction


def export_reference(trace: Path, junit: Path, destination: Path) -> None:
    """Publish a fresh, sealed trace and its XML without replacing evidence."""
    destination.mkdir(parents=True, exist_ok=True)
    targets = [(junit, destination / junit.name), (trace, destination / trace.name)]
    if any(target.exists() for _, target in targets):
        raise FileExistsError(f"reference export already exists in {destination}; use a new run directory")
    for source, target in targets:
        with target.open("xb") as output:
            output.write(source.read_bytes())


def test_export_reference_preserves_pair_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    trace, junit = source / "trace.json", source / "suite.xml"
    trace.write_bytes(b'{"fresh": true}\n')
    junit.write_bytes(b"<testsuite/>\n")
    destination = tmp_path / "export"
    export_reference(trace, junit, destination)
    assert (destination / trace.name).read_bytes() == trace.read_bytes()
    assert (destination / junit.name).read_bytes() == junit.read_bytes()
    trace.write_bytes(b'{"changed": true}\n')
    with pytest.raises(FileExistsError, match="new run directory"):
        export_reference(trace, junit, destination)
    assert (destination / trace.name).read_bytes() == b'{"fresh": true}\n'


def test_reference_counter_holds_wraps_and_resets():
    model = CounterRegisterModel()
    for _ in range(260):
        model.write(STEP, 1)
    model.write(STEP, 0)
    assert model.read(COUNT) == 4
    assert model.read(STEPS) == 261
    model.write(RESET, 1)
    assert model.read(COUNT) == model.read(STEPS) == 0


def test_same_checker_rejects_injected_read_divergence():
    log = expected_log(50, 1234)
    check_log(log, 50, 1234)
    index = next(i for i, item in enumerate(log) if item.kind == "read" and item.address == COUNT)
    original = log.entries[index]
    log.entries[index] = Transaction(original.kind, original.address, original.value ^ 1)
    with pytest.raises(AssertionError, match="divergence"):
        check_log(log, 50, 1234)


def test_source_hashes_ignore_only_checkout_line_endings(monkeypatch, tmp_path):
    monkeypatch.setattr(checker, "TEMPLATE", tmp_path)
    monkeypatch.setattr(checker, "SOURCES", ("design.sv",))
    source = tmp_path / "design.sv"
    source.write_bytes(b"module design;\nendmodule\n")
    expected = checker.source_hashes()
    source.write_bytes(b"module design;\r\nendmodule\r\n")
    assert checker.source_hashes() == expected
    source.write_bytes(b"module other;\nendmodule\n")
    assert checker.source_hashes() != expected


@pytest.mark.parametrize("relative", [
    "tests/counter/model.py", "tests/counter/stimulus.py", "tests/common/transport.py",
])
def test_reference_tracks_shared_model_stimulus_and_log_checker(relative, monkeypatch, tmp_path):
    assert relative in checker.SOURCES
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_text("# original\n", encoding="utf-8")
    monkeypatch.setattr(checker, "TEMPLATE", tmp_path)
    monkeypatch.setattr(checker, "SOURCES", (relative,))
    before = checker.source_hashes()
    source.write_text("# changed\n", encoding="utf-8")
    assert checker.source_hashes() != before


@pytest.mark.simulation
@pytest.mark.parametrize("simulator", ["verilator", "icarus"])
def test_jtag_counter_simulation(simulator, tmp_path):
    if shutil.which("iverilog" if simulator == "icarus" else simulator) is None:
        pytest.skip(f"{simulator} is not installed")
    # Each test owns its build, trace and JUnit. Never delete a prior reference
    # that an operator may already have selected for physical replay.
    results = tmp_path / "results"
    results.mkdir()
    trace = results / f"jtag-counter-{simulator}.json"
    result = subprocess.run(
        ["make", "sim", "DESIGN=jtag_counter", "TOPLEVEL=jtag_counter_sim_top", "WIDTH=8",
         f"SIM={simulator}", f"SIM_BUILD={(tmp_path / 'build').as_posix()}",
         f"RESULTS_DIR={results.as_posix()}"],
        cwd=TEMPLATE, capture_output=True, text=True, timeout=180,
        env={**os.environ, "JTAG_TRACE": str(trace)},
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-6000:]
    assert trace.is_file() and trace.stat().st_size > 0
    junit = results / f"cocotb-{simulator}-jtag_counter-w8.xml"
    check_simulation_suite(junit)
    reference = json.loads(trace.read_text(encoding="utf-8"))
    reference["suite"] = {"file": junit.name, "sha256": hashlib.sha256(junit.read_bytes()).hexdigest()}
    trace.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    export = os.environ.get("JTAG_EXPORT_DIR")
    if export:
        destination = Path(export)
        if not destination.is_absolute():
            destination = TEMPLATE / destination
        export_reference(trace, junit, destination)
