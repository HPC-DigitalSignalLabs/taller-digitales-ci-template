# SPDX-License-Identifier: Apache-2.0
"""One transaction checker for RTL simulation and later physical replay."""

import hashlib
from pathlib import Path
from xml.etree import ElementTree

from jtag_counter.model import CounterRegisterModel
from jtag_counter.stimulus import commands
from transport import TransactionLog, compare_logs

TEMPLATE = Path(__file__).resolve().parents[2]
SOURCE_FORMAT = "sha256-utf8-lf"
REQUIRED_CASES = {
    "test_shared_counter_sequence", "test_wrap_reset_and_exact_step_count",
    "test_backpressure_and_partial_frame_reset", "test_stalls_fail_closed_until_reset",
    "test_bad_commands_addresses_and_data_never_alias",
}
SOURCES = (
    "src/counter.sv", "src/wr_decoder.sv", "src/jtag_counter.sv",
    # Transitive dependencies are part of the simulation reference too: changing
    # the shared model/stimulus or log comparison must invalidate an old trace.
    "tests/counter/model.py", "tests/counter/stimulus.py",
    "tests/common/transport.py",
    "tests/jtag_counter/model.py", "tests/jtag_counter/stimulus.py",
    "tests/jtag_counter/checker.py", "tests/jtag_counter/tb_jtag_counter.py",
    "tests/jtag_counter/jtag_counter_sim_top.sv",
)


def source_hashes():
    # Git checkouts on Windows and Linux may differ only in line endings.
    return {name: hashlib.sha256((TEMPLATE / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for name in SOURCES}


def check_simulation_suite(path: Path) -> None:
    cases = list(ElementTree.parse(path).getroot().iter("testcase"))
    if not REQUIRED_CASES <= {case.get("name") for case in cases}:
        raise AssertionError("simulation suite omits required bridge scenarios")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise AssertionError("simulation suite contains failed, errored or skipped scenarios")


def expected_log(cycles: int, seed: int) -> TransactionLog:
    reference = CounterRegisterModel()
    for operation, address, value in commands(cycles, seed):
        if operation == "write":
            reference.write(address, value)
        else:
            reference.read(address)
    return reference.log


def check_log(observed: TransactionLog, cycles: int, seed: int) -> None:
    divergences = compare_logs(expected_log(cycles, seed), observed)
    if divergences:
        raise AssertionError(f"{len(divergences)} counter divergence(s): {divergences[0]}")
