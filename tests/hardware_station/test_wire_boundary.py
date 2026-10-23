# SPDX-License-Identifier: Apache-2.0
"""Frontera W/R probada en Python, no concordancia con silicio."""

from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from hardware import StreamTransport
from transport import MemoryTransport, TransportError, compare_logs
from test_transport import stimulus_and_check


class FragmentedDevice:
    """Doble de prueba incremental; NO sustituye un decodificador RTL."""

    def __init__(self, fragment=1):
        self.fragment = fragment
        self.pending = bytearray()
        self.output = bytearray()
        self.cells = {}
        self.wire = bytearray()

    def write(self, data):
        part = data[:self.fragment]
        self.wire.extend(part)
        self.pending.extend(part)
        while self.pending:
            command = self.pending[0]
            assert command in (ord("W"), ord("R"))
            length = 9 if command == ord("W") else 5
            if len(self.pending) < length:
                break
            address = int.from_bytes(self.pending[1:5], "big")
            if command == ord("W"):
                self.cells[address] = int.from_bytes(self.pending[5:9], "big")
            else:
                self.output.extend(struct.pack(">I", self.cells.get(address, 0)))
            del self.pending[:length]
        return len(part)

    def read(self, count):
        count = min(count, self.fragment)
        result = bytes(self.output[:count])
        del self.output[:count]
        return result


@pytest.mark.parametrize("fragment", [1, 2, 3, 4, 9, 32])
def test_reuses_existing_stimulus_and_checker_with_fragmentation(fragment):
    reference = MemoryTransport()
    device = FragmentedDevice(fragment)
    stream = StreamTransport(device, owns_stream=False)
    stimulus_and_check(reference)
    stimulus_and_check(stream)
    assert compare_logs(reference.log, stream.log) == []
    assert not device.pending


def test_exact_big_endian_frames_and_word_addresses():
    device = FragmentedDevice()
    stream = StreamTransport(device)
    stream.write(0x01020304, 0x89ABCDEF)
    assert stream.read(0x01020304) == 0x89ABCDEF
    assert bytes(device.wire) == bytes.fromhex("57 01 02 03 04 89 ab cd ef 52 01 02 03 04")
    with pytest.raises(ValueError, match="32 bits"):
        stream.read(0x100000000)


@pytest.mark.parametrize("bad_count", [0, -1, 100, True, "9"])
def test_invalid_partial_write_poisoned_and_never_logged(bad_count):
    device = FragmentedDevice()
    device.write = lambda data: bad_count
    stream = StreamTransport(device)
    with pytest.raises(TransportError):
        stream.write(0, 1)
    with pytest.raises(TransportError, match="desincronizado"):
        stream.read(0)
    assert len(stream.log) == 0


@pytest.mark.parametrize("reply", [b"", b"12345", "abcd"])
def test_bad_response_requires_decoder_reset(reply):
    device = FragmentedDevice()
    device.read = lambda count: reply
    stream = StreamTransport(device)
    with pytest.raises(TransportError):
        stream.read(0)
    with pytest.raises(TransportError, match="desincronizado"):
        stream.write(0, 1)
    assert len(stream.log) == 0


def test_backend_exception_is_transport_failure():
    class Broken:
        def write(self, data):
            raise OSError("cable desconectado")
    stream = StreamTransport(Broken())
    with pytest.raises(TransportError, match="cable desconectado"):
        stream.read(0)
