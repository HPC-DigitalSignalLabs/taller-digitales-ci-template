# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""A design-shaped proof of the transport-reuse property (OE3, ADR-0004 D1, ADR-0009).

``test_transport.py`` already shows that a checker can run over two transports, but
its checker is a toy address/value loop: it writes ``addr*0x1111_1111`` and reads it
back. A plain memory round-trips those bytes no matter what, so that test proves the
*plumbing* is transport-agnostic but not that a **real** checker — one that judges a
device against a golden model, the way OE3 requires — is.

This module closes that gap. It defines a register-mapped reference block that
resembles an actual ``lib/`` macro (a memory-mapped accumulator with a control
register, an opcode register and status flags), a seeded stimulus generator, and a
checker written **once** against the ``Transport`` API and the golden model. That same
checker then runs **unchanged** over two genuinely different transports:

* :class:`MmioTransport` — in-process word access, standing in for how a CocoTB
  testbench drives the DUT directly in simulation.
* :class:`~hardware.StreamTransport` over :class:`AccumulatorDeviceStream` — the fixed
  JTAG/serial wire protocol, standing in for the FPGA over JTAG.

Both reach the *same* device core, so any behavioural drift is the transport's fault,
which is exactly what must be ruled out. Finally an injected design bug is shown to be
caught over one transport, proving the checker judges behaviour and not just framing.

No CocoTB, no hardware, no Quartus: this is pure Python and runs in the verification
container. It is deterministic and seeded (``TB_SEED``), so it never flakes (OE2).
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hardware import CMD_READ, CMD_WRITE, StreamTransport  # noqa: E402
from transport import MemoryTransport, Transport, compare_logs  # noqa: E402

# Fixed, replayable seed. Overridable for a sweep, but constant by default so the
# suite is deterministic (OE2 flaky-rate target). 20260803 = project start date.
SEED = int(os.environ.get("TB_SEED", "20260803"))

# --- The reference block's register map --------------------------------------
# A small memory-mapped accumulator. Writing OPERAND executes ``acc = acc <op> operand``
# and refreshes the status flags. This is deliberately shaped like a real peripheral so
# the checker resembles a design-level checker, not an address/value echo.

ACC_WIDTH = 32
MASK = (1 << ACC_WIDTH) - 1

REG_CTRL = 0x0     # write bit0 (clear) pulses a reset of the accumulator
REG_OP = 0x1       # write the opcode
REG_OPERAND = 0x2  # write the operand -> executes the operation
REG_ACC = 0x3      # read-only: current accumulator value
REG_STATUS = 0x4   # read-only: bit0 zero flag, bit1 carry/borrow flag

CTRL_CLEAR = 0x1

OP_ADD = 0b000
OP_SUB = 0b001
OP_AND = 0b010
OP_OR = 0b011
OP_XOR = 0b100
_ALL_OPS = (OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR)


# --- Golden model (pure Python, knows nothing about transports) --------------


class AccumulatorModel:
    """Reference behaviour of the register-mapped accumulator block."""

    def __init__(self) -> None:
        self.acc = 0
        self.carry = False

    def clear(self) -> None:
        self.acc = 0
        self.carry = False

    def apply(self, op: int, operand: int) -> None:
        operand &= MASK
        if op == OP_ADD:
            total = self.acc + operand
            self.acc = total & MASK
            self.carry = bool((total >> ACC_WIDTH) & 0x1)
        elif op == OP_SUB:
            self.carry = self.acc >= operand  # no borrow
            self.acc = (self.acc - operand) & MASK
        elif op == OP_AND:
            self.acc &= operand
            self.carry = False
        elif op == OP_OR:
            self.acc |= operand
            self.carry = False
        elif op == OP_XOR:
            self.acc ^= operand
            self.carry = False

    @property
    def status(self) -> int:
        return (1 if self.acc == 0 else 0) | ((1 if self.carry else 0) << 1)


# --- Device core (the DUT stand-in reached through every transport) ----------


class _AccumulatorCore:
    """In-memory implementation of the accumulator RTL, shared by both transports.

    Both the direct transport and the streamed transport talk to *this* object, so a
    difference between the two transports can only be a transport bug, never a design
    difference. Set ``buggy=True`` to inject a design defect (SUB behaves like ADD).
    """

    def __init__(self, *, buggy: bool = False) -> None:
        self.buggy = buggy
        self.acc = 0
        self.op = OP_ADD
        self.carry = False
        self._scratch: dict[int, int] = {}

    def read(self, address: int) -> int:
        if address == REG_ACC:
            return self.acc
        if address == REG_STATUS:
            return (1 if self.acc == 0 else 0) | ((1 if self.carry else 0) << 1)
        return self._scratch.get(address, 0)

    def write(self, address: int, value: int) -> None:
        value &= MASK
        if address == REG_CTRL:
            self._scratch[address] = value
            if value & CTRL_CLEAR:
                self.acc = 0
                self.carry = False
        elif address == REG_OP:
            self._scratch[address] = value
            self.op = value & 0x7
        elif address == REG_OPERAND:
            self._scratch[address] = value
            self._execute(value)
        # REG_ACC / REG_STATUS are read-only; writes are ignored, as in the RTL.

    def _execute(self, operand: int) -> None:
        op = self.op
        if op == OP_ADD:
            total = self.acc + operand
            self.acc = total & MASK
            self.carry = bool((total >> ACC_WIDTH) & 0x1)
        elif op == OP_SUB:
            if self.buggy:
                # Injected defect: subtraction wired as addition.
                total = self.acc + operand
                self.acc = total & MASK
                self.carry = bool((total >> ACC_WIDTH) & 0x1)
            else:
                self.carry = self.acc >= operand
                self.acc = (self.acc - operand) & MASK
        elif op == OP_AND:
            self.acc &= operand
            self.carry = False
        elif op == OP_OR:
            self.acc |= operand
            self.carry = False
        elif op == OP_XOR:
            self.acc ^= operand
            self.carry = False


class MmioTransport(Transport):
    """Direct in-process word access to a device core (the simulator-side plane)."""

    def __init__(self, core: _AccumulatorCore) -> None:
        super().__init__(word_bits=ACC_WIDTH)
        self._core = core

    def read_word(self, address: int) -> int:
        return self._core.read(address)

    def write_word(self, address: int, value: int) -> None:
        self._core.write(address, value)


class AccumulatorDeviceStream:
    """A byte-stream device that speaks the JTAG/serial wire protocol.

    Wraps the same :class:`_AccumulatorCore`, so :class:`StreamTransport` reaches
    identical behaviour by a completely different path (framed bytes vs direct calls).
    """

    def __init__(self, core: _AccumulatorCore) -> None:
        self._core = core
        self._out = bytearray()

    def write(self, data: bytes) -> int:
        cmd, rest = data[:1], data[1:]
        if cmd == CMD_WRITE:
            address, value = struct.unpack(">II", rest)
            self._core.write(address, value)
        elif cmd == CMD_READ:
            (address,) = struct.unpack(">I", rest)
            self._out += struct.pack(">I", self._core.read(address))
        else:
            raise AssertionError(f"unknown command byte {cmd!r}")
        return len(data)

    def read(self, count: int) -> bytes:
        take = min(count, len(self._out))
        chunk = bytes(self._out[:take])
        del self._out[:take]
        return chunk


# --- Stimulus, written once --------------------------------------------------


@dataclass(frozen=True)
class AccStep:
    """One accumulator operation: apply ``op`` with ``operand``."""

    op: int
    operand: int


def accumulator_sequence(*, steps: int, seed: int) -> list[AccStep]:
    """Directed corner cases followed by seeded random operations."""
    if steps < 1:
        raise ValueError("steps must be positive")
    rng = Random(seed)
    directed = [
        AccStep(OP_ADD, 100),        # plain add
        AccStep(OP_SUB, 30),         # add vs sub diverge here if SUB is broken
        AccStep(OP_ADD, MASK),       # carry out
        AccStep(OP_SUB, 0x7F),       # borrow path
        AccStep(OP_AND, 0x0F0F_0F0F),
        AccStep(OP_OR, 0xF0F0_F0F0),
        AccStep(OP_XOR, 0xFFFF_FFFF),
    ]
    sequence = directed[:steps]
    while len(sequence) < steps:
        sequence.append(
            AccStep(op=rng.choice(_ALL_OPS), operand=rng.randrange(1 << ACC_WIDTH))
        )
    return sequence


# --- The checker, written ONCE against Transport + model ---------------------


@dataclass(frozen=True)
class AccMismatch:
    index: int
    field: str
    expected: int
    actual: int


def run_accumulator_check(
    transport: Transport, model: AccumulatorModel, sequence: list[AccStep]
) -> list[AccMismatch]:
    """Drive a device through ``transport`` and judge it against ``model``.

    This function only ever calls ``transport.read`` / ``transport.write`` and the
    golden model. It has no idea whether it is talking to a simulator or to silicon,
    which is precisely the OE3 property. It is reused verbatim over every transport.
    """
    mismatches: list[AccMismatch] = []

    transport.write(REG_CTRL, CTRL_CLEAR)
    model.clear()

    for index, step in enumerate(sequence):
        transport.write(REG_OP, step.op)
        transport.write(REG_OPERAND, step.operand)
        model.apply(step.op, step.operand)

        actual_acc = transport.read(REG_ACC)
        actual_status = transport.read(REG_STATUS)

        if actual_acc != model.acc:
            mismatches.append(AccMismatch(index, "acc", model.acc, actual_acc))
        if actual_status != model.status:
            mismatches.append(
                AccMismatch(index, "status", model.status, actual_status)
            )

    return mismatches


# --- The properties OE3 depends on -------------------------------------------


def test_same_design_checker_runs_over_two_transports() -> None:
    """One design-level checker, two unrelated transports, both agree with the model."""
    sequence = accumulator_sequence(steps=64, seed=SEED)

    direct = MmioTransport(_AccumulatorCore())
    streamed = StreamTransport(
        AccumulatorDeviceStream(_AccumulatorCore()), owns_stream=False
    )

    sim_mismatches = run_accumulator_check(direct, AccumulatorModel(), sequence)
    hw_mismatches = run_accumulator_check(streamed, AccumulatorModel(), sequence)

    # The checker — unchanged — judges both planes correctly.
    assert sim_mismatches == []
    assert hw_mismatches == []

    # And both transports produced byte-identical transaction streams: this is the
    # sim/hardware agreement verdict (OE3), reused from the transport layer.
    assert compare_logs(direct.log, streamed.log) == []
    assert len(direct.log) == len(streamed.log)


def test_injected_design_bug_is_detected_on_one_transport() -> None:
    """A real design defect must be caught by the reused checker, not just framing."""
    sequence = accumulator_sequence(steps=64, seed=SEED)

    buggy = StreamTransport(
        AccumulatorDeviceStream(_AccumulatorCore(buggy=True)), owns_stream=False
    )
    mismatches = run_accumulator_check(buggy, AccumulatorModel(), sequence)

    # The broken SUB diverges from the model; the checker flags it and locates it.
    assert mismatches, "the injected SUB-as-ADD defect went undetected"
    assert any(m.field == "acc" for m in mismatches)
    assert mismatches[0].index == 1  # directed SUB step, where add and sub disagree

    # Cross-check via the transaction logs: a good reference and the buggy device
    # diverge, which is the dry-run scenario-6 detection path (ADR-0004).
    good = MmioTransport(_AccumulatorCore())
    run_accumulator_check(good, AccumulatorModel(), sequence)
    assert compare_logs(good.log, buggy.log) != []


def test_reuse_is_deterministic_across_runs() -> None:
    """Same seed, same transaction log every time (OE2 flaky rate ≈ 0)."""
    sequence = accumulator_sequence(steps=48, seed=SEED)
    signatures = []
    for _ in range(8):
        t = MmioTransport(_AccumulatorCore())
        run_accumulator_check(t, AccumulatorModel(), sequence)
        signatures.append([(e.kind, e.address, e.value) for e in t.log])
    assert all(sig == signatures[0] for sig in signatures)


def test_checker_only_touches_the_transport_api() -> None:
    """Guard: the checker must be usable on a bare Transport it has never seen.

    ``MemoryTransport`` is a dumb dict with no accumulator logic, so the checker must
    report mismatches rather than crash. If the checker had reached past the Transport
    API into a specific backend, this would fail with an AttributeError instead.
    """
    sequence = accumulator_sequence(steps=8, seed=SEED)
    mismatches = run_accumulator_check(MemoryTransport(), AccumulatorModel(), sequence)
    # A plain memory is not an accumulator, so of course it disagrees with the model;
    # the point is that the reused checker ran end to end over an unrelated transport.
    assert mismatches
