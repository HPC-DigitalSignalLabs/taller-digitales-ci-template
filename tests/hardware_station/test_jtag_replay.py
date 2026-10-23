# SPDX-License-Identifier: Apache-2.0
"""Replay preconditions and evidence tests, never a physical cable test."""

from dataclasses import asdict
import json
from pathlib import Path
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TEMPLATE / "tools"))
import jtag_counter_replay as replay
from jtag_counter.checker import REQUIRED_CASES, expected_log
from jtag_counter.model import COUNT, CounterRegisterModel


def reference_file(tmp_path):
    junit = tmp_path / "cocotb-test-fixture.xml"
    junit.write_text("<testsuite>" + "".join(
        f'<testcase name="{name}"/>' for name in sorted(REQUIRED_CASES)
    ) + "</testsuite>")
    data = {
        "schema": 1, "kind": "jtag_counter_simulation", "simulator": "Verilator",
        "cycles": 10, "seed": 42, "sources": replay.source_hashes(),
        "source_format": replay.SOURCE_FORMAT,
        "suite": {"file": junit.name, "sha256": replay.sha256(junit)},
        "transactions": [asdict(item) for item in expected_log(10, 42)],
    }
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def test_check_does_not_open_hardware(monkeypatch, tmp_path):
    path, _ = reference_file(tmp_path)
    def forbidden(**kwargs):
        pytest.fail("check must not open a cable")
    monkeypatch.setattr(replay, "JtagUartTransport", forbidden)
    output = tmp_path / "check.json"
    assert replay.main(["check", "--reference", str(path), "--output", str(output)]) == 0
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "reference_valid"
    assert not evidence["hardware_accessed"]


@pytest.mark.parametrize("fault", ["sources", "transactions", "simulator", "suite"])
def test_reference_is_rejected_when_stale_or_corrupt(tmp_path, fault):
    path, data = reference_file(tmp_path)
    if fault == "sources":
        data["sources"] = {}
    elif fault == "transactions":
        data["transactions"][2]["value"] ^= 1
    elif fault == "simulator":
        data["simulator"] = "Python model"
    else:
        junit = tmp_path / data["suite"]["file"]
        junit.write_text('<testsuite><testcase name="test_shared_counter_sequence"/></testsuite>')
        data["suite"]["sha256"] = replay.sha256(junit)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert replay.main(["check", "--reference", str(path)]) == 1


def test_run_requires_explicit_operator_confirmation(tmp_path):
    path, _ = reference_file(tmp_path)
    with pytest.raises(SystemExit) as error:
        replay.main(["run", "--reference", str(path)])
    assert error.value.code == 2


def test_dirty_station_is_rejected_before_any_cable_access(monkeypatch, tmp_path):
    path, _ = reference_file(tmp_path)
    manifest = tmp_path / "station-evidence.json"
    manifest.write_text(json.dumps({"kind": "fpga_station_compile", "status": "compiled_dirty"}))
    def forbidden(**kwargs):
        pytest.fail("dirty station must not open a cable")
    monkeypatch.setattr(replay, "JtagUartTransport", forbidden)
    output = tmp_path / "result.json"
    assert replay.main([
        "run", "--reference", str(path), "--station-manifest", str(manifest),
        "--confirm-board-ready", "--board-revision", "test-double", "--output", str(output),
    ]) == 1
    evidence = json.loads(output.read_text())
    assert not evidence["hardware_accessed"]
    assert "clean" in evidence["error"]


def test_replay_uses_same_checker_and_detects_design_divergence(tmp_path):
    path, _ = reference_file(tmp_path)
    reference, expected = replay.load_reference(path)
    replay.replay(CounterRegisterModel(), reference, expected)
    class BrokenCounter(CounterRegisterModel):
        def read_word(self, address):
            value = super().read_word(address)
            return value ^ 1 if address == COUNT else value
    with pytest.raises(AssertionError, match="divergence"):
        replay.replay(BrokenCounter(), reference, expected)


@pytest.mark.parametrize("document", [[], None, "not a trace"])
def test_malformed_reference_produces_failed_evidence(tmp_path, document):
    source = tmp_path / "bad.json"
    source.write_text(json.dumps(document))
    output = tmp_path / "result.json"
    assert replay.main(["check", "--reference", str(source), "--output", str(output)]) == 1
    assert json.loads(output.read_text())["status"] == "failed"


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_station_checks_artifact_hashes_before_gate(monkeypatch, tmp_path, separator):
    required = (
        "src/counter.sv", "src/wr_decoder.sv", "src/jtag_counter.sv",
        "quartus/de1_soc_jtag.qsf", "quartus/boards/de1_soc_jtag_top.sv",
        "quartus/targets/de1_soc_jtag.sdc",
    )
    artifacts = {}
    (tmp_path / "output_files").mkdir()
    for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof"):
        name = f"output_files/station.{suffix}"
        path = tmp_path / name
        path.write_bytes(b"explicit test fixture")
        artifacts[name.replace("/", separator)] = replay.sha256(path)
    manifest = tmp_path / "station-evidence.json"
    manifest.write_text(json.dumps({
        "kind": "fpga_station_compile", "board": "de1_soc", "profile": "jtag-counter",
        "status": "compiled", "gate_accepted": True, "artifacts": artifacts,
        "inputs": {"dirty": False, "commit": "test-fixture",
                   "raw_sha256": {name: replay.sha256(TEMPLATE / name) for name in required}},
    }))
    from types import SimpleNamespace
    calls = []
    def gate(*args):
        calls.append(args)
        return SimpleNamespace(tool={"profile": "jtag-counter"})
    monkeypatch.setattr(replay, "validate", gate)
    assert not replay.check_station(manifest)["configured_bitstream_verified"]
    assert len(calls) == 1
    (tmp_path / "output_files" / "station.sof").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        replay.check_station(manifest)
    assert len(calls) == 1
