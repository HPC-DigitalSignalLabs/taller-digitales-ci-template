#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Native Windows deployment adapter; each action needs its own approved job.

No service/driver installation. Programming is volatile SOF only; replay still
requires an operator's post-program KEY0 checkpoint. Existing station/replay
commands remain the authorities on source and transaction evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.etree import ElementTree as ET

from deployment_guard import output, require

TEMPLATE = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def invoke(argv: list[str], log: Path, timeout: int, expected_code: int = 0) -> str:
    try:
        result = subprocess.run(argv, cwd=TEMPLATE, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        def text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        write(log, {"argv": argv, "returncode": None, "error": str(exc),
                    "timed_out": isinstance(exc, subprocess.TimeoutExpired),
                    "stdout": text(getattr(exc, "stdout", None)),
                    "stderr": text(getattr(exc, "stderr", None))})
        raise
    write(log, {"argv": argv, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr})
    require(result.returncode == expected_code, f"{Path(argv[0]).name} failed ({result.returncode}); see {log}")
    return result.stdout


def reference_checks(bundle: Path, evidence: Path, station: bool) -> None:
    for sim in ("verilator", "icarus"):
        command = [sys.executable, str(TEMPLATE / "tools/jtag_counter_replay.py"), "check",
                   "--reference", str(bundle / "references" / f"jtag-counter-{sim}.json")]
        if station:
            command += ["--station-manifest", str(bundle / "station-evidence.json")]
        invoke(command, evidence / f"check-{sim}.json", 60)


def validate_bundle(bundle: Path, expected_sof: str, expected_manifest: str, source_sha: str) -> dict:
    require(bool(re.fullmatch(r"[0-9a-f]{64}", expected_sof)), "Missing approved SOF SHA256.")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", expected_manifest)), "Missing approved manifest SHA256.")
    manifest = bundle / "station-evidence.json"
    require(digest(manifest) == expected_manifest, "Station manifest differs from the approved build.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    require(data["inputs"]["commit"] == source_sha, "Build commit differs from approved source_sha.")
    require(data["inputs"]["dirty"] is False and data.get("gate_accepted") is True,
            "Build must be clean and gate-accepted.")
    require(digest(bundle / "output_files/station.sof") == expected_sof, "SOF differs from approved hash.")
    require(data["artifacts"]["output_files/station.sof"] == expected_sof, "SOF not bound to manifest.")
    return data


def build_binding(bundle: Path) -> dict:
    data = json.loads((bundle / "deployment-binding.json").read_text(encoding="utf-8"))
    for key, variable in (("source_sha", "SOURCE_SHA"), ("verification_run_id", "VERIFICATION_RUN_ID"),
                           ("reference_artifact_id", "REFERENCE_ARTIFACT_ID"),
                           ("build_run_id", "BUILD_RUN_ID"), ("build_run_attempt", "BUILD_RUN_ATTEMPT"),
                           ("sof_sha256", "SOF_SHA256")):
        require(str(data.get(key)) == os.environ[variable], f"Build lineage mismatch: {key}")
    return data


def programming_argv(bin_dir: Path, sof: Path, cable: str, index: str) -> list[str]:
    require(bool(cable.strip()) and len(cable) <= 128 and not any(c in cable for c in "\r\n"),
            "Explicit enumerated cable name required.")
    require(index in ("1", "2"), "FPGA JTAG index must be confirmed explicitly (1 or 2), not inferred.")
    require(not any(c in str(sof) for c in ";@\r\n"), "SOF path contains programmer delimiters.")
    return [str(bin_dir / "quartus_pgm.exe"), "-m", "JTAG", "-c", cable, "-o", f"p;{sof}@{index}"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("compile", "program", "replay"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--programming-evidence", type=Path)
    args = parser.parse_args()
    require(os.name == "nt", "Privileged FPGA operations require the provisioned native Windows station.")
    source_sha = os.environ["SOURCE_SHA"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=TEMPLATE, text=True).strip()
    require(head == source_sha, "Checkout differs from authorized immutable source SHA.")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "."], cwd=TEMPLATE, text=True)
    require(not dirty.strip(), "Template checkout is dirty; never compile/program exploratory sources.")
    bundle, evidence = args.bundle.resolve(), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    bin_dir = Path(os.environ["QUARTUS_BIN"]).resolve()
    require(bin_dir.is_dir(), "QUARTUS_BIN must identify the provisioned Quartus 18.1 bin64.")
    if args.action == "compile":
        require(args.references is not None, "Compile requires the downloaded simulation artifact.")
        bundle.mkdir(parents=True, exist_ok=False)
        refs = bundle / "references"
        refs.mkdir()
        for sim in ("verilator", "icarus"):
            for name in (f"jtag-counter-{sim}.json", f"cocotb-{sim}-jtag_counter-w8.xml"):
                shutil.copyfile(args.references / name, refs / name)
        reference_checks(bundle, evidence, station=False)
        text = invoke(
            [sys.executable, str(TEMPLATE / "tools/fpga_station.py"), "compile", "--template", str(TEMPLATE),
             "--quartus-bin", str(bin_dir), "--board", "de1_soc", "--profile", "jtag-counter", "--timeout", "1800"],
            evidence / "compile.json", 1860)
        compiled = json.loads(text)
        workspace = Path(compiled["workspace"])
        require(workspace.resolve().is_relative_to((TEMPLATE / "results/fpga-station").resolve()),
                "Station returned a workspace outside its private compile directory.")
        shutil.copyfile(workspace / "station-evidence.json", bundle / "station-evidence.json")
        (bundle / "output_files").mkdir()
        for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof", "flow.rpt.provenance.json"):
            shutil.copyfile(workspace / f"output_files/station.{suffix}", bundle / f"output_files/station.{suffix}")
        sof = digest(bundle / "output_files/station.sof")
        manifest_hash = digest(bundle / "station-evidence.json")
        validate_bundle(bundle, sof, manifest_hash, source_sha)
        reference_checks(bundle, evidence, station=True)
        write(bundle / "deployment-binding.json",
              {"source_sha": source_sha, "verification_run_id": os.environ["VERIFICATION_RUN_ID"],
               "reference_artifact_id": os.environ["REFERENCE_ARTIFACT_ID"],
               "build_run_id": os.environ["GITHUB_RUN_ID"], "build_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "sof_sha256": sof, "manifest_sha256": manifest_hash})
        output("sof_sha256", sof)
        output("manifest_sha256", manifest_hash)
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(f"## Review before programming\nSource: `{source_sha}`\n\nSOF SHA256: `{sof}`\n\n"
                         f"Station manifest SHA256: `{manifest_hash}`\n\n"
                         "Review the uploaded flow/map/sta reports, board revision/pinout, cable/index, "
                         "power and disconnected GPIO before a separate **program** dispatch "
                         f"with build_run_id=`{os.environ['GITHUB_RUN_ID']}` and the exact SOF hash. "
                         "Compilation and the provenance gate do not prove timing closure.\n")
        return
    sof = os.environ["SOF_SHA256"]
    binding = build_binding(bundle)
    manifest_hash = binding["manifest_sha256"]
    validate_bundle(bundle, sof, manifest_hash, source_sha)
    reference_checks(bundle, evidence, station=True)
    revision, cable = os.environ["BOARD_REVISION"], os.environ["JTAG_CABLE"]
    require(bool(re.fullmatch(r"[A-Za-z0-9._-]{1,32}", revision)), "Explicit PCB revision required.")
    if args.action == "program":
        # preflight deliberately returns 1 even on a prepared station: it never
        # claims hardware validation. Check specific observed fields, not "green".
        probe = json.loads(invoke(
            [sys.executable, str(TEMPLATE / "tools/fpga_station.py"), "preflight",
             "--quartus-bin", str(bin_dir), "--board", "de1_soc", "--profile", "jtag-counter"],
            evidence / "preflight-command.json", 120, expected_code=1))
        write(evidence / "preflight.json", probe)
        require(probe.get("quartus_18_1_detected") is True, "Preflight did not confirm Quartus 18.1.")
        require(probe.get("jtag", {}).get("state") == "enumerated", "No enumerated JTAG chain.")
        require(probe.get("python_jtag_driver", {}).get("native_symbols_resolved") is True and
                probe.get("python_jtag_driver", {}).get("adapter_api_resolved") is True,
                "Native JTAG driver ABI/API preflight failed.")
        invoke(programming_argv(bin_dir, bundle / "output_files/station.sof",
                                cable, os.environ["FPGA_INDEX"]), evidence / "programmer.json", 120)
        # The programmer return code is evidence of configuration, NOT a readback.
        require(digest(bundle / "output_files/station.sof") == sof, "SOF changed during programming.")
        write(evidence / "programming.json",
              {"source_sha": source_sha, "sof_sha256": sof, "manifest_sha256": manifest_hash,
               "board_revision": revision, "cable": cable, "fpga_index": os.environ["FPGA_INDEX"],
               "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "build_run_id": os.environ["BUILD_RUN_ID"], "build_run_attempt": os.environ["BUILD_RUN_ATTEMPT"],
               "verification_run_id": os.environ["VERIFICATION_RUN_ID"],
               "programmer_returncode": 0, "readback_verified": False})
        output("programming_sha256", digest(evidence / "programming.json"))
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("## Separate replay dispatch required\n"
                         f"programming_run_id=`{os.environ['GITHUB_RUN_ID']}`\n\n"
                         f"programming_sha256=`{digest(evidence / 'programming.json')}`\n\n"
                         "Keep board/cable/power unchanged and exclusive. Close other UART sessions. "
                         "Press/release KEY0; check LEDR[8]=1 and LEDR[9]=0. "
                         "Dispatch **replay** with key0_confirmed=true only after this physical checkpoint. "
                         "No automatic reset/readback claim.\n")
    else:
        require(os.environ.get("KEY0_CONFIRMED") == "true", "Replay requires explicit post-program KEY0 confirmation.")
        require(args.programming_evidence is not None, "Replay requires separately approved programming evidence.")
        path = args.programming_evidence
        require(digest(path) == os.environ["PROGRAMMING_SHA256"], "Programming evidence hash mismatch.")
        programmed = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (("source_sha", source_sha), ("sof_sha256", sof), ("board_revision", revision),
                           ("cable", cable), ("fpga_index", os.environ["FPGA_INDEX"]),
                           ("run_id", os.environ["PROGRAMMING_RUN_ID"]),
                           ("run_attempt", os.environ["PROGRAMMING_RUN_ATTEMPT"]),
                           ("build_run_id", os.environ["BUILD_RUN_ID"]),
                           ("build_run_attempt", os.environ["BUILD_RUN_ATTEMPT"]),
                           ("verification_run_id", os.environ["VERIFICATION_RUN_ID"]),
                           ("manifest_sha256", manifest_hash)):
            require(programmed.get(key) == value, f"Programming evidence mismatch: {key}")
        require(programmed.get("programmer_returncode") == 0, "No successful programming evidence.")
        invoke(
            [sys.executable, str(TEMPLATE / "tools/jtag_counter_replay.py"), "run",
             "--reference", str(bundle / "references/jtag-counter-verilator.json"),
             "--station-manifest", str(bundle / "station-evidence.json"),
             "--board-revision", revision, "--confirm-board-ready", "--cable", cable,
             "--device=-1", "--instance", "0", "--timeout", "2",
             "--output", str(evidence / "replay.json")], evidence / "replay-command.json", 300)
        result = json.loads((evidence / "replay.json").read_text(encoding="utf-8"))
        require(result.get("status") == "transaction_agreement_passed", "Replay did not establish agreement.")


if __name__ == "__main__":
    # Always leave a failing JUnit on exceptions; no skipped mandatory stages.
    args_for_report = sys.argv
    evidence_dir = None
    if "--evidence" in args_for_report:
        evidence_dir = Path(args_for_report[args_for_report.index("--evidence") + 1])
    suite = ET.Element("testsuite", name="fpga-deployment", tests="1", failures="0")
    case = ET.SubElement(suite, "testcase", name=args_for_report[1] if len(args_for_report) > 1 else "missing-action")
    try:
        main()
    except BaseException as exc:
        suite.set("failures", "1")
        ET.SubElement(case, "failure", message=str(exc)).text = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if evidence_dir:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            ET.ElementTree(suite).write(evidence_dir / "junit.xml", encoding="utf-8", xml_declaration=True)
