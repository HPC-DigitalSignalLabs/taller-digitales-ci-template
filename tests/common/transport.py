# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Transport-agnostic transaction interface for the host to FPGA channel (OE3).

The point of this module is that stimulus generators and checkers are written
once and never learn how bits reach the device under test. A checker written
against ``Transport`` runs unchanged against the Verilator simulation, against a
Cyclone V SoC target board over the HPS-to-FPGA bridge, and against a serial
link.

That indirection is what makes the Fase 0 transport decision reversible, and it
is what OE3 measures as stimulus reuse. See
``docs/decisions/ADR-0004-canal-host-fpga.md``.

Register semantics are deliberately minimal: word-addressed reads and writes
over an Avalon-MM style address space. Anything a backend cannot express in
those terms does not belong in a testbench.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterable, Iterator


class TransportError(RuntimeError):
    """A transport-level failure: link down, timeout, framing error."""


@dataclass(frozen=True)
class Transaction:
    """One register access, recorded for the divergence report.

    Attributes:
        kind: ``"read"`` or ``"write"``.
        address: Word address in the device address space.
        value: Value written, or value read back.
    """

    kind: str
    address: int
    value: int


@dataclass
class TransactionLog:
    """Ordered record of everything a transport did.

    Comparing two logs produced by the same stimulus on two different transports
    is how sim/hardware agreement is measured (OE3).
    """

    entries: list[Transaction] = field(default_factory=list)

    def record(self, kind: str, address: int, value: int) -> None:
        self.entries.append(Transaction(kind=kind, address=address, value=value))

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self.entries)


class Transport(abc.ABC):
    """A bidirectional word-addressed channel to a device under test.

    Backends implement :meth:`read_word` and :meth:`write_word`. Everything else
    is provided here so behaviour cannot drift between backends.
    """

    def __init__(self, word_bits: int = 32) -> None:
        if word_bits <= 0 or word_bits % 8:
            raise ValueError(f"word_bits must be a positive multiple of 8, got {word_bits}")
        self.word_bits = word_bits
        self.log = TransactionLog()

    @property
    def mask(self) -> int:
        """Bit mask for one word."""
        return (1 << self.word_bits) - 1

    # --- backend contract ---

    @abc.abstractmethod
    def read_word(self, address: int) -> int:
        """Read one raw word. Backends must not log; the wrapper does."""

    @abc.abstractmethod
    def write_word(self, address: int, value: int) -> None:
        """Write one raw word. Backends must not log; the wrapper does."""

    # --- uniform API used by testbenches ---

    def read(self, address: int) -> int:
        """Read one word and record the transaction."""
        self._check_address(address)
        value = self.read_word(address) & self.mask
        self.log.record("read", address, value)
        return value

    def write(self, address: int, value: int) -> None:
        """Write one word and record the transaction."""
        self._check_address(address)
        if not 0 <= value <= self.mask:
            raise ValueError(
                f"value {value:#x} does not fit in {self.word_bits} bits"
            )
        self.write_word(address, value)
        self.log.record("write", address, value)

    def write_burst(self, address: int, values: Iterable[int]) -> None:
        """Write consecutive words starting at ``address``."""
        for offset, value in enumerate(values):
            self.write(address + offset, value)

    def read_burst(self, address: int, count: int) -> list[int]:
        """Read ``count`` consecutive words starting at ``address``."""
        if count < 0:
            raise ValueError(f"count must be non-negative, got {count}")
        return [self.read(address + offset) for offset in range(count)]

    def _check_address(self, address: int) -> None:
        if not isinstance(address, int) or address < 0:
            raise ValueError(f"address must be a non-negative int, got {address!r}")

    # --- lifecycle ---

    def close(self) -> None:
        """Release any underlying resource. Safe to call more than once."""

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class MemoryTransport(Transport):
    """Reference backend backed by a dict, for testing the layer itself.

    It is also the substitute device used to prove that a checker really is
    transport-agnostic before any hardware exists.
    """

    def __init__(self, word_bits: int = 32) -> None:
        super().__init__(word_bits=word_bits)
        self._cells: dict[int, int] = {}

    def read_word(self, address: int) -> int:
        return self._cells.get(address, 0)

    def write_word(self, address: int, value: int) -> None:
        self._cells[address] = value


@dataclass(frozen=True)
class Divergence:
    """One point where two transaction logs disagree."""

    index: int
    reference: Transaction | None
    observed: Transaction | None

    def __str__(self) -> str:
        return (
            f"[{self.index}] reference={self.reference!r} observed={self.observed!r}"
        )


def compare_logs(
    reference: TransactionLog, observed: TransactionLog
) -> list[Divergence]:
    """Compare a simulation log against a hardware log.

    This is the OE3 sim/hardware agreement check. An empty result means the
    device reproduced the simulation exactly.

    Args:
        reference: Log captured in simulation.
        observed: Log captured on hardware.

    Returns:
        Every point of disagreement, in order. Empty means full agreement.
    """
    divergences: list[Divergence] = []
    ref = list(reference)
    obs = list(observed)
    for index in range(max(len(ref), len(obs))):
        a = ref[index] if index < len(ref) else None
        b = obs[index] if index < len(obs) else None
        if a != b:
            divergences.append(Divergence(index=index, reference=a, observed=b))
    return divergences
