# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Hardware backends for the host to FPGA transaction channel (OE3, Fase 3).

Every backend here implements :class:`~transport.Transport`, so the stimulus
generators and checkers written for simulation run against them unchanged. That
is the property OE3 measures, and it is why the transport decision recorded in
``docs/decisions/ADR-0004-canal-host-fpga.md`` is reversible.

Wire protocol
-------------
All stream-oriented backends (JTAG UART, serial) share one framing, so the
device-side RTL is written once:

    write : 0x57 'W' | addr[31:0] big-endian | data[31:0] big-endian
    read  : 0x52 'R' | addr[31:0] big-endian            -> data[31:0] big-endian

Fixed-width frames keep the FPGA-side decoder to a small state machine, which
matters because that decoder is itself course material.

None of these classes import their driver at module import time. The container
used for open-source functional verification has no Quartus and no board
attached, and importing this module there must stay harmless.
"""

from __future__ import annotations

import struct
from typing import Any, Protocol

from transport import Transport, TransportError

CMD_WRITE = b"W"
CMD_READ = b"R"

#: Bytes returned for a read response.
RESPONSE_BYTES = 4


class ByteStream(Protocol):
    """Minimal duplex byte stream, satisfied by pyserial and by JTAG UART."""

    def write(self, data: bytes) -> Any: ...
    def read(self, count: int) -> bytes: ...


class StreamTransport(Transport):
    """Transport over any duplex byte stream, using the fixed frame above.

    Args:
        stream: An open duplex byte stream.
        word_bits: Data word width. Only 32 is currently framed.
        owns_stream: Whether :meth:`close` should close the stream.
    """

    def __init__(
        self, stream: ByteStream, word_bits: int = 32, owns_stream: bool = True
    ) -> None:
        if word_bits != 32:
            raise ValueError("the wire protocol currently frames 32-bit words only")
        super().__init__(word_bits=word_bits)
        self._stream = stream
        self._owns_stream = owns_stream
        self._failed = False

    def _check_address(self, address: int) -> None:
        super()._check_address(address)
        if address > 0xFFFF_FFFF:
            raise ValueError("la dirección no cabe en los 32 bits del protocolo")

    def _write_frame(self, frame: bytes) -> None:
        """Completar escrituras parciales sin repetir bytes ya enviados.

        El backend debe acotar sus operaciones bloqueantes. Si pierde bytes,
        este protocolo sin delimitador no permite resincronizar por sí solo:
        se requiere restablecer también el decodificador del dispositivo.
        """
        if self._failed:
            raise TransportError("enlace desincronizado; restablezca el canal y el decodificador")
        offset = 0
        try:
            while offset < len(frame):
                written = self._stream.write(frame[offset:])
                # Algunos drivers no devuelven longitud. None conserva su
                # contrato de escritura completa, no constituye ACK del DUT.
                if written is None:
                    return
                if not isinstance(written, int) or isinstance(written, bool) or not 0 < written <= len(frame) - offset:
                    raise TransportError(f"longitud de escritura inválida: {written!r}")
                offset += written
        except Exception as exc:
            self._failed = True
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"falló la escritura del frame: {exc}") from exc

    def write_word(self, address: int, value: int) -> None:
        self._write_frame(CMD_WRITE + struct.pack(">II", address, value))

    def read_word(self, address: int) -> int:
        self._write_frame(CMD_READ + struct.pack(">I", address))
        try:
            raw = self._read_exactly(RESPONSE_BYTES)
        except Exception as exc:
            self._failed = True
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"falló la lectura de la respuesta: {exc}") from exc
        return int(struct.unpack(">I", raw)[0])

    def _read_exactly(self, count: int) -> bytes:
        """Read exactly ``count`` bytes or raise.

        A short read is a link failure, not a value. Returning padded data here
        would surface as a mysterious functional mismatch much later.
        """
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self._stream.read(remaining)
            if not isinstance(chunk, bytes) or len(chunk) > remaining:
                raise TransportError("el backend devolvió una respuesta de tipo o longitud inválidos")
            if not chunk:
                got = count - remaining
                raise TransportError(
                    f"link returned {got} of {count} bytes before going quiet; "
                    "check that the device is configured and the design is running"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        if self._owns_stream:
            closer = getattr(self._stream, "close", None)
            if callable(closer):
                closer()


class JtagUartTransport(StreamTransport):
    """Primary hardware transport: Intel JTAG UART over the on-board USB-Blaster II.

    Requires an ``altera_jtag_uart`` instance in the design and a Quartus
    installation on the host, because the ``intel_jtag_uart`` package binds to
    Quartus's native JTAG Atlantic library. That dependency is permitted: this runs on
    a lab station, which is the proprietary domain (ADR-0004).

    Args:
        instance: JTAG UART instance index when several are present.
        cable: Cable name to disambiguate multiple attached boards.
        device: Device index on the JTAG chain; -1 asks the driver to select.
        timeout: Deadline for opening the cable and each stream operation.
    """

    def __init__(
        self, instance: int = 0, cable: str | None = None,
        device: int = -1, timeout: float = 2.0,
    ) -> None:
        from jtag_stream import JtagStream

        stream = JtagStream(cable, device, instance, timeout)
        super().__init__(stream=stream, owns_stream=True)


class SerialTransport(StreamTransport):
    """Fallback hardware transport: UART over the GPIO header.

    Fully open-source on the host (pyserial). This is the degradation path in
    ADR-0004 if the JTAG experiments miss their time box.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise TransportError(
                "pyserial is not installed (pip install pyserial)."
            ) from exc

        try:
            stream = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        except Exception as exc:  # pragma: no cover - needs hardware
            raise TransportError(f"could not open serial port {port}: {exc}") from exc

        super().__init__(stream=stream, owns_stream=True)


class HpsBridgeTransport(Transport):
    """Additional transport: HPS-to-FPGA lightweight bridge, from Linux on the HPS.

    Runs on the target board's own Cyclone V HPS Linux and needs no proprietary
    software at all. Maps the lightweight bridge window and performs Avalon-MM
    word accesses.

    Args:
        base: Physical address of the lightweight bridge window.
        span: Bytes to map.
        device: Backing device file.

    Note:
        The FPGA must already be configured and the bridge enabled, otherwise
        accesses fault or read as garbage.
    """

    #: Lightweight HPS-to-FPGA bridge base on Cyclone V SoC.
    LWFPGA_BASE = 0xFF200000
    DEFAULT_SPAN = 0x0020_0000

    def __init__(
        self,
        base: int = LWFPGA_BASE,
        span: int = DEFAULT_SPAN,
        device: str = "/dev/mem",
    ) -> None:
        super().__init__(word_bits=32)
        self._span = span
        try:
            import mmap
            import os

            self._os = os
            self._fd = os.open(device, os.O_RDWR | os.O_SYNC)
            self._map = mmap.mmap(
                self._fd,
                span,
                mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
                offset=base,
            )
        except (OSError, ValueError) as exc:  # pragma: no cover - needs the board
            raise TransportError(
                f"could not map {device} at {base:#x} (+{span:#x}): {exc}. "
                "This backend only runs on a supported Cyclone V SoC HPS Linux, as root, "
                "with the FPGA configured and the bridge enabled."
            ) from exc

    def _offset(self, address: int) -> int:
        offset = address * 4
        if offset + 4 > self._span:
            raise TransportError(
                f"word address {address:#x} is outside the {self._span:#x}-byte bridge window"
            )
        return offset

    def read_word(self, address: int) -> int:
        offset = self._offset(address)
        return int(struct.unpack("<I", self._map[offset : offset + 4])[0])

    def write_word(self, address: int, value: int) -> None:
        offset = self._offset(address)
        self._map[offset : offset + 4] = struct.pack("<I", value)

    def close(self) -> None:  # pragma: no cover - needs the board
        mapping = getattr(self, "_map", None)
        if mapping is not None and not mapping.closed:
            mapping.close()
        fd = getattr(self, "_fd", None)
        if fd is not None:
            try:
                self._os.close(fd)
            except OSError:
                pass
            self._fd = None
