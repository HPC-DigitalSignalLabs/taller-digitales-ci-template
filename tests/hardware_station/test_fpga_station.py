# SPDX-License-Identifier: Apache-2.0
"""Pruebas de estación sin acceso al cable ni programación."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TEMPLATE / "tools"))
import fpga_station as station


def response(stdout="", code=0, stderr=""):
    return {"returncode": code, "stdout": stdout, "stderr": stderr, "timed_out": False}


@pytest.mark.parametrize("output,code,state", [
    ("No JTAG hardware available", 4, "absent"),
    ("1) USB-BlasterII [USB-0]\n  02D020DD 5CSE", 0, "enumerated"),
    ("", 0, "error_or_unrecognized"),
    ("1) USB-BlasterII", 4, "error_or_unrecognized"),
    ("Error: unable to connect", 0, "error_or_unrecognized"),
])
def test_jtag_enumeration_is_not_board_identification(output, code, state):
    assert station.jtag_state(response(output, code)) == state


def test_preflight_never_claims_hardware_validation(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, cwd, timeout=30, env=None):
        calls.append(argv)
        return response("Version 18.1.0 Build 625 Lite Edition" if "--version" in argv else "1) USB-BlasterII")

    monkeypatch.setattr(station, "run", fake_run)
    monkeypatch.setattr(station.importlib.util, "find_spec", lambda name: None)
    evidence = station.preflight(TEMPLATE, tmp_path, None, 2)
    assert evidence["quartus_18_1_detected"]
    assert evidence["jtag"]["state"] == "enumerated"
    assert evidence["board"]["physical_identity"] == "unconfirmed"
    assert not evidence["can_attempt_compile_only"]
    assert not evidence["hardware_validation_supported"]
    assert not evidence["programming_supported"]
    assert not evidence["python_jtag_driver"]["installed"]
    assert not evidence["python_jtag_driver"]["native_symbols_resolved"]
    assert any("API" in blocker for blocker in evidence["blockers"])
    assert any("símbolos nativos" in blocker for blocker in evidence["blockers"])
    assert len(calls) == 2
    assert calls[0][1:] == ["--version"]
    assert len(calls[1]) == 1


def test_python_jtag_driver_check_verifies_abi_when_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(station.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(station, "run", lambda *a, **kw: response("ABI_RESOLVED\n"))
    result = station.python_jtag_driver_check(tmp_path, 2)
    assert result["installed"]
    assert result["native_symbols_resolved"]
    assert "windows_abi_and_api_verified" not in result


def test_python_jtag_driver_check_reports_abi_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(station.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(station, "run", lambda *a, **kw: response("", code=1, stderr="AttributeError"))
    result = station.python_jtag_driver_check(tmp_path, 2)
    assert result["installed"]
    assert not result["native_symbols_resolved"]


def test_loaded_driver_does_not_remove_transport_blocker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        station, "run",
        lambda argv, *a, **kw: response(
            "Version 18.1.0" if "--version" in argv else "1) DE-SoC [USB-1]"
        ),
    )
    monkeypatch.setattr(
        station, "python_jtag_driver_check",
        lambda *a: {"installed": True, "native_symbols_resolved": True, "adapter_api_resolved": True},
    )
    evidence = station.preflight(TEMPLATE, tmp_path, "de1_soc", 2)
    assert evidence["can_attempt_compile_only"]
    assert not evidence["hardware_validation_supported"]
    assert any("API" in blocker for blocker in evidence["blockers"])
    assert not any("No se resolvieron" in blocker for blocker in evidence["blockers"])


def test_driver_adapter_probe_never_opens_cable(monkeypatch, tmp_path):
    calls = []
    def fake_run(argv, *args, **kwargs):
        calls.append(argv)
        return response("ABI_RESOLVED\nADAPTER_API_RESOLVED\n")
    monkeypatch.setattr(station.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(station, "run", fake_run)
    result = station.python_jtag_driver_check(tmp_path, 2)
    assert result["adapter_api_resolved"] and result["native_symbols_resolved"]
    assert "driver_api()" in calls[0][2]
    assert "JtagUartTransport(" not in calls[0][2]


@pytest.mark.parametrize("profile,top", [
    ("panel", "de1_soc_top"), ("jtag-counter", "de1_soc_jtag_top"),
])
def test_preflight_top_metadata_matches_selected_profile(monkeypatch, tmp_path, profile, top):
    monkeypatch.setattr(
        station, "run",
        lambda argv, *a, **kw: response(
            "Version 18.1.0" if "--version" in argv else "1) DE-SoC [USB-1]"
        ),
    )
    monkeypatch.setattr(
        station, "python_jtag_driver_check",
        lambda *a: {"installed": True, "native_symbols_resolved": True, "adapter_api_resolved": True},
    )
    evidence = station.preflight(TEMPLATE, tmp_path, "de1_soc", 2, profile)
    assert evidence["board"]["target"]["top"] == top
    assert station.BOARDS["de1_soc"]["top"] == "de1_soc_top"
    assert not evidence["hardware_validation_supported"]


def test_adapter_failure_does_not_erase_native_import_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(station.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(station, "run", lambda *a, **kw: response("ABI_RESOLVED\n", code=1))
    result = station.python_jtag_driver_check(tmp_path, 2)
    assert result["native_symbols_resolved"]
    assert not result["adapter_api_resolved"]


def test_jtag_profile_is_opt_in_and_board_specific(tmp_path):
    assert station.project_qsf("de1_soc", "panel") == "quartus/de1_soc.qsf"
    assert station.project_qsf("de1_soc", "jtag-counter") == "quartus/de1_soc_jtag.qsf"
    with pytest.raises(station.StationError, match="de1_soc"):
        station.project_qsf("de10_standard", "jtag-counter")
    with pytest.raises(station.StationError, match="Perfil"):
        station.project_qsf("de1_soc", "typo")
    (tmp_path / "quartus").mkdir()
    source = TEMPLATE / "quartus" / "de1_soc_jtag.qsf"
    (tmp_path / "quartus" / source.name).write_bytes(source.read_bytes())
    result = station.prepare_project(tmp_path, "de1_soc", "jtag-counter")
    assert result["selected_qsf"] == "quartus/de1_soc_jtag.qsf"
    text = (tmp_path / "station.qsf").read_text()
    assert "QIP_FILE" in text and "de1_soc_jtag_top" in text


def test_wrong_version_blocks_compile(monkeypatch, tmp_path):
    monkeypatch.setattr(station, "run", lambda *a, **kw: response("Version 23.1"))
    with pytest.raises(station.StationError, match="18.1"):
        station.compile_design(TEMPLATE, tmp_path, "de1_soc", 1, True)


def test_plan_explicit_paths_and_no_programmer(tmp_path):
    plan = station.commands(TEMPLATE, tmp_path / "Quartus con espacios", "de1_soc", tmp_path / "captura")
    assert plan["compile_argv"][1:] == ["--flow", "compile", "station", "-c", "station"]
    assert plan["compile_cwd"] == str(tmp_path / "captura")
    assert "--allow-dirty" not in plan["gate_argv"]
    assert "tools/fpga_station.py" in plan["gate_argv"]
    assert "quartus_pgm" not in json.dumps(plan)


def test_project_preserves_target_and_constraints(tmp_path):
    (tmp_path / "quartus").mkdir()
    text = (TEMPLATE / "quartus" / "de1_soc.qsf").read_text(encoding="utf-8")
    (tmp_path / "quartus" / "de1_soc.qsf").write_text(text, encoding="utf-8")
    project = station.prepare_project(tmp_path, "de1_soc")
    result = (tmp_path / "station.qsf").read_text(encoding="utf-8")
    assert "POST_FLOW_SCRIPT_FILE" not in "\n".join(
        line for line in result.splitlines() if not line.lstrip().startswith("#")
    )
    assert "PIN_AF14" in result
    assert "quartus/targets/de1_soc.sdc" in result
    assert project["generated_qsf_sha256"] == station.sha256(tmp_path / "station.qsf")


def test_snapshot_preserves_repo_relative_paths(monkeypatch, tmp_path):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "counter.sv").write_bytes(b"module counter; endmodule\n")
    workspace = tmp_path / "capture"
    workspace.mkdir()

    def fake_git(cwd, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args[0] == "status":
            return ""
        if args[0] == "ls-files":
            return "src/counter.sv\0"
        if args[0] == "hash-object":
            return "b" * 40
        raise AssertionError(args)

    monkeypatch.setattr(station, "git", fake_git)
    evidence = station.snapshot(template, workspace, False)
    assert evidence["sources"] == {"template/src/counter.sv": "b" * 40}
    assert not evidence["dirty"]
    assert (workspace / "src" / "counter.sv").read_bytes() == (template / "src" / "counter.sv").read_bytes()


def test_snapshot_refuses_dirty_or_untracked_sources(monkeypatch, tmp_path):
    def fake_git(cwd, *args):
        return str(tmp_path) if args == ("rev-parse", "--show-toplevel") else "?? src/new.sv"
    monkeypatch.setattr(station, "git", fake_git)
    with pytest.raises(station.StationError, match="sin confirmar"):
        station.snapshot(tmp_path, tmp_path / "capture", False)


@pytest.mark.parametrize("mode", ["process_failure", "missing_reports", "failed_flow", "changed_source", "success", "dirty"])
def test_compile_provenance_requires_complete_fresh_evidence(monkeypatch, tmp_path, mode):
    (tmp_path / "quartus").mkdir()
    text = (TEMPLATE / "quartus" / "de1_soc.qsf").read_text(encoding="utf-8")
    (tmp_path / "quartus" / "de1_soc.qsf").write_text(text, encoding="utf-8")

    def fake_snapshot(template, workspace, allow_dirty):
        (workspace / "quartus").mkdir()
        qsf = workspace / "quartus" / "de1_soc.qsf"
        qsf.write_text(text, encoding="utf-8")
        return {
            "commit": "a" * 40, "dirty": mode == "dirty",
            "sources": {"quartus/de1_soc.qsf": "b" * 40},
            "raw_sha256": {"quartus/de1_soc.qsf": station.sha256(qsf)},
            "repo_root": str(tmp_path), "status": "",
        }

    def fake_run(argv, cwd, timeout=30):
        if "--version" in argv:
            return response("Version 18.1.0 Build 625")
        if "--flow" in argv:
            if mode == "process_failure":
                return response("Error", 1)
            output = cwd / "output_files"
            output.mkdir()
            if mode != "missing_reports":
                for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof"):
                    (output / f"station.{suffix}").write_text(
                        "; Flow Status ; Failed ;" if mode == "failed_flow" else "; Flow Status ; Successful ;",
                        encoding="utf-8",
                    )
            if mode == "changed_source":
                (cwd / "quartus" / "de1_soc.qsf").write_text("changed", encoding="utf-8")
            return response()
        assert "--report" in argv
        assert "--allow-dirty" not in argv
        return response(code=1 if mode == "dirty" else 0)

    monkeypatch.setattr(station, "snapshot", fake_snapshot)
    monkeypatch.setattr(station, "run", fake_run)
    monkeypatch.setattr(station, "git", lambda *args: "c" * 40)
    evidence, code = station.compile_design(tmp_path, tmp_path, "de1_soc", 2, mode == "dirty")
    workspace = Path(evidence["workspace"])
    assert (workspace / "station-evidence.json").is_file()
    sidecar = workspace / "output_files" / "station.flow.rpt.provenance.json"
    if mode in ("success", "dirty"):
        assert set(evidence["artifacts"]) == {
            f"output_files/station.{suffix}" for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof")
        }
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert data["commit"] == "a" * 40
        assert data["dirty"] == (mode == "dirty")
        assert data["sources"] == {"quartus/de1_soc.qsf": "b" * 40}
        assert code == (1 if mode == "dirty" else 0)
    else:
        assert code == 1
        assert not sidecar.exists()
    assert not evidence["hardware_validated"]
    assert not evidence["programmed"]


def test_missing_executable_and_timeout_are_structured(tmp_path):
    absent = station.run([str(tmp_path / "missing.exe")], tmp_path, 1)
    assert absent["returncode"] is None
    timed_out = station.run([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.05)
    assert timed_out["timed_out"]


def test_unknown_board_does_not_default_to_de1_soc():
    with pytest.raises(SystemExit) as error:
        station.main(["plan", "--template", str(TEMPLATE)])
    assert error.value.code == 2
