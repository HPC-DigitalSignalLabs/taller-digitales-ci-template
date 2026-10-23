# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""The transport layer and the reuse property it exists to guarantee (OE3).

The load-bearing test here is
:func:`test_same_checker_runs_over_two_different_transports`. If a checker can
run unchanged over two unrelated transports, the abstraction is real. If it
cannot, OE3's stimulus-reuse metric is unreachable no matter what the
documentation claims.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from hardware import CMD_READ, CMD_WRITE, StreamTransport  # noqa: E402
from transport import (  # noqa: E402
    MemoryTransport,
    Transport,
    TransportError,
    compare_logs,
)


class FakeDeviceStream:
    """A byte-stream device implementing the wire protocol, for tests.

    Stands in for the FPGA-side decoder so the framing can be exercised with no
    hardware attached.
    """

    def __init__(self, corrupt_after: int | None = None) -> None:
        self.cells: dict[int, int] = {}
        self._out = bytearray()
        self._corrupt_after = corrupt_after
        self._responses = 0

    def write(self, data: bytes) -> int:
        cmd, rest = data[:1], data[1:]
        if cmd == CMD_WRITE:
            address, value = struct.unpack(">II", rest)
            self.cells[address] = value
        elif cmd == CMD_READ:
            (address,) = struct.unpack(">I", rest)
            if self._corrupt_after is not None and self._responses >= self._corrupt_after:
                self._responses += 1
                return len(data)  # go quiet: simulates a dropped link
            self._responses += 1
            self._out += struct.pack(">I", self.cells.get(address, 0))
        else:
            raise AssertionError(f"unknown command byte {cmd!r}")
        return len(data)

    def read(self, count: int) -> bytes:
        take = min(count, len(self._out))
        chunk = bytes(self._out[:take])
        del self._out[:take]
        return chunk


def stimulus_and_check(transport: Transport) -> None:
    """A checker written once, with no knowledge of how bits travel.

    This is the pattern every testbench in the project follows: it talks only to
    the Transport API, so it is reusable against the simulator and against the
    board.
    """
    for address in range(8):
        transport.write(address, address * 0x1111_1111 & transport.mask)
    for address in range(8):
        expected = address * 0x1111_1111 & transport.mask
        actual = transport.read(address)
        assert actual == expected, f"@{address:#x}: expected {expected:#x}, got {actual:#x}"


# --- The property OE3 depends on ---------------------------------------------


def test_same_checker_runs_over_two_different_transports() -> None:
    """One checker, two unrelated transports, identical transaction logs."""
    memory = MemoryTransport()
    stream = StreamTransport(FakeDeviceStream(), owns_stream=False)

    stimulus_and_check(memory)
    stimulus_and_check(stream)

    assert compare_logs(memory.log, stream.log) == []
    assert len(memory.log) == len(stream.log) == 16


def test_compare_logs_reports_a_value_divergence() -> None:
    """Injected divergence must be detected and located (OE3, dry-run 6)."""
    reference = MemoryTransport()
    stimulus_and_check(reference)

    device = FakeDeviceStream()
    observed = StreamTransport(device, owns_stream=False)
    stimulus_and_check(observed)
    # Corrupt one cell and re-read it, as a faulty design would.
    device.cells[3] = 0xDEAD_BEEF
    observed.read(3)

    divergences = compare_logs(reference.log, observed.log)
    assert len(divergences) == 1
    assert divergences[0].index == 16
    assert divergences[0].reference is None
    assert divergences[0].observed is not None
    assert divergences[0].observed.value == 0xDEAD_BEEF


def test_compare_logs_detects_a_mismatched_read() -> None:
    a = MemoryTransport()
    b = MemoryTransport()
    a.write(0, 1)
    a.read(0)
    b.write(0, 2)
    b.read(0)

    divergences = compare_logs(a.log, b.log)
    assert [d.index for d in divergences] == [0, 1]


# --- Framing ------------------------------------------------------------------


def test_stream_transport_round_trips_all_values() -> None:
    t = StreamTransport(FakeDeviceStream(), owns_stream=False)
    for value in (0, 1, 0x7FFF_FFFF, 0x8000_0000, 0xFFFF_FFFF):
        t.write(0x20, value)
        assert t.read(0x20) == value


def test_burst_helpers_are_consistent() -> None:
    t = StreamTransport(FakeDeviceStream(), owns_stream=False)
    payload = [0xA, 0xB, 0xC, 0xD]
    t.write_burst(0x100, payload)
    assert t.read_burst(0x100, len(payload)) == payload


def test_short_read_raises_rather_than_returning_padding() -> None:
    """A dropped link must fail loudly, not look like a functional mismatch."""
    t = StreamTransport(FakeDeviceStream(corrupt_after=0), owns_stream=False)
    with pytest.raises(TransportError, match="before going quiet"):
        t.read(0)


# --- API hygiene --------------------------------------------------------------


def test_oversized_value_is_rejected() -> None:
    t = MemoryTransport(word_bits=8)
    t.write(0, 0xFF)
    with pytest.raises(ValueError, match="does not fit"):
        t.write(0, 0x100)


@pytest.mark.parametrize("address", [-1, "0x10", None])
def test_invalid_address_is_rejected(address: object) -> None:
    t = MemoryTransport()
    with pytest.raises(ValueError):
        t.read(address)  # type: ignore[arg-type]


def test_unset_cells_read_as_zero() -> None:
    assert MemoryTransport().read(0xDEAD) == 0


def test_word_width_must_be_a_byte_multiple() -> None:
    with pytest.raises(ValueError):
        MemoryTransport(word_bits=12)


def test_stream_transport_rejects_unframed_widths() -> None:
    with pytest.raises(ValueError, match="32-bit words only"):
        StreamTransport(FakeDeviceStream(), word_bits=64)


def test_transport_is_a_context_manager() -> None:
    with MemoryTransport() as t:
        t.write(0, 42)
        assert t.read(0) == 42


def test_hardware_backends_import_without_quartus_or_a_board() -> None:
    """Importing hardware.py must be harmless inside the verification container."""
    import hardware

    assert hasattr(hardware, "JtagUartTransport")
    assert hasattr(hardware, "HpsBridgeTransport")
    assert hardware.HpsBridgeTransport.LWFPGA_BASE == 0xFF200000


def test_determinism_across_repeated_runs() -> None:
    """Same stimulus, same log, every time (OE2 flaky rate)."""
    logs = []
    for _ in range(10):
        t = MemoryTransport()
        stimulus_and_check(t)
        logs.append([(e.kind, e.address, e.value) for e in t.log])
    assert all(log == logs[0] for log in logs)
