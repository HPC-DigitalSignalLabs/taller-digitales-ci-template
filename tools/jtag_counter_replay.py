# SPDX-License-Identifier: Apache-2.0
"""Check a simulated trace or explicitly replay it on an already configured board.

Never programs an FPGA. Physical replay requires an operator confirmation,
a clean station build and the exact current simulation/checker sources.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from xml.etree import ElementTree

TEMPLATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEMPLATE / "tests"))
sys.path.insert(0, str(TEMPLATE / "tests" / "common"))

from artifact_gate import GateError, validate
from fpga_station import SOURCE_SPECS, sha256
from hardware import JtagUartTransport
from jtag_counter.checker import SOURCE_FORMAT, check_log, check_simulation_suite, source_hashes
from jtag_counter.stimulus import commands
from transport import Transaction, TransactionLog, TransportError, compare_logs


def load_reference(path: Path) -> tuple[dict, TransactionLog]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("kind") != "jtag_counter_simulation":
        raise ValueError("not a JTAG-counter simulation trace")
    if data.get("source_format") != SOURCE_FORMAT or data.get("sources") != source_hashes():
        raise ValueError("stale simulation sources; rerun both open simulators")
    if type(data.get("cycles")) is not int or data["cycles"] <= 0 or type(data.get("seed")) is not int:
        raise ValueError("trace requires positive cycles and an integer seed")
    if data.get("simulator") not in ("Verilator", "Icarus Verilog"):
        raise ValueError("reference must identify an open RTL simulator")
    suite = data["suite"]
    name = suite["file"]
    if not isinstance(name, str) or "/" in name or "\\" in name or not name.endswith(".xml"):
        raise ValueError("simulation suite must be an adjacent XML file")
    junit = path.parent / name
    if sha256(junit) != suite["sha256"]:
        raise ValueError("simulation suite XML changed")
    check_simulation_suite(junit)
    log = TransactionLog()
    if not isinstance(data.get("transactions"), list):
        raise ValueError("reference transactions must be a list")
    for item in data["transactions"]:
        if (not isinstance(item, dict) or item["kind"] not in ("read", "write")
                or type(item["address"]) is not int or not 0 <= item["address"] <= 0xFFFF_FFFF
                or type(item["value"]) is not int or not 0 <= item["value"] <= 0xFFFF_FFFF):
            raise ValueError("invalid reference transaction")
        log.entries.append(Transaction(**item))
    check_log(log, data["cycles"], data["seed"])
    return data, log


def check_station(manifest: Path) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("kind") != "fpga_station_compile" or data.get("board") != "de1_soc"
            or data.get("profile") != "jtag-counter" or data.get("status") != "compiled"
            or data.get("gate_accepted") is not True or data["inputs"]["dirty"] is not False):
        raise ValueError("physical replay requires a clean, accepted DE1-SoC jtag-counter build")
    for relative in ("src/counter.sv", "src/wr_decoder.sv", "src/jtag_counter.sv",
                     "quartus/de1_soc_jtag.qsf", "quartus/boards/de1_soc_jtag_top.sv",
                     "quartus/targets/de1_soc_jtag.sdc"):
        if relative not in data["inputs"]["raw_sha256"]:
            raise ValueError(f"station evidence omits {relative}")
    for relative, digest in data["inputs"]["raw_sha256"].items():
        source = (TEMPLATE / relative).resolve()
        if not source.is_relative_to(TEMPLATE.resolve()) or sha256(source) != digest:
            raise ValueError(f"station input changed: {relative}")
    artifacts = {name.replace("\\", "/"): digest for name, digest in data["artifacts"].items()}
    if len(artifacts) != len(data["artifacts"]):
        raise ValueError("ambiguous station artifact paths")
    for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof"):
        relative = f"output_files/station.{suffix}"
        if sha256(manifest.parent / relative) != artifacts[relative]:
            raise ValueError(f"station artifact changed: {relative}")
    gate = validate(TEMPLATE, manifest.parent / "output_files" / "station.flow.rpt", "HEAD", SOURCE_SPECS)
    if gate.tool.get("profile") != "jtag-counter":
        raise ValueError("artifact provenance does not identify the jtag-counter profile")
    return {
        "manifest_sha256": sha256(manifest),
        "compile_commit": data["inputs"]["commit"],
        "bitstream_sha256": artifacts["output_files/station.sof"],
        "configured_bitstream_verified": False,
    }


def replay(transport, reference: dict, expected: TransactionLog) -> None:
    for operation, address, value in commands(reference["cycles"], reference["seed"]):
        if operation == "write":
            transport.write(address, value)
        else:
            transport.read(address)
    check_log(transport.log, reference["cycles"], reference["seed"])
    divergences = compare_logs(expected, transport.log)
    if divergences:
        raise AssertionError(f"simulation/hardware divergence: {divergences[0]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run"))
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--station-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--board-revision")
    parser.add_argument("--confirm-board-ready", action="store_true",
                        help="Operator confirms PCB/pins, exact SOF loaded, and KEY0 reset. No programming is performed.")
    parser.add_argument("--cable")
    parser.add_argument("--device", type=int, default=-1)
    parser.add_argument("--instance", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=2)
    args = parser.parse_args(argv)
    if args.action == "run" and not (
        args.confirm_board_ready and args.station_manifest and args.output and args.board_revision
    ):
        parser.error("run requires --confirm-board-ready, --board-revision, --station-manifest and --output")
    evidence = {"schema": 1, "kind": "jtag_counter_replay", "status": "failed",
                "programmed": False, "hardware_accessed": False}
    observed = None
    code = 1
    try:
        reference, expected = load_reference(args.reference)
        evidence.update(reference_sha256=sha256(args.reference),
                        cycles=reference["cycles"], seed=reference["seed"])
        if args.station_manifest:
            evidence["station"] = check_station(args.station_manifest)
        if args.action == "check":
            evidence["status"] = "reference_valid"
        else:
            evidence["board_revision"] = args.board_revision
            evidence["hardware_accessed"] = True
            observed = JtagUartTransport(
                instance=args.instance, cable=args.cable, device=args.device, timeout=args.timeout,
            )
            with observed:
                replay(observed, reference, expected)
            evidence["status"] = "transaction_agreement_passed"
        code = 0
    except (OSError, ValueError, TypeError, KeyError, AssertionError, ElementTree.ParseError,
            TransportError, GateError) as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if observed is not None:
            evidence["transactions"] = [asdict(item) for item in observed.log]
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
