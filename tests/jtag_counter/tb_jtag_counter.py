# SPDX-License-Identifier: Apache-2.0
"""Avalon UART bus functional model; not a model of the USB cable or silicon."""

from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from jtag_counter.checker import SOURCE_FORMAT, check_log, source_hashes
from jtag_counter.model import COUNT, ID, IDENTITY, RESET, STEP, STEPS
from jtag_counter.stimulus import commands
from transport import TransactionLog


class UartSlave:
    def __init__(self, dut):
        self.dut = dut
        self.rx = deque()
        self.tx = deque()
        self.stall = False
        self.space = True
        self.cycle = 0

    async def run(self):
        held = None
        while True:
            await FallingEdge(self.dut.clk)
            self.cycle += 1
            reset = not int(self.dut.rst_n.value)
            pins = tuple(int(getattr(self.dut, name).value) for name in (
                "m_chipselect", "m_address", "m_read_n", "m_write_n", "m_writedata",
            ))
            if held is not None and not reset and not int(self.dut.fault.value):
                assert pins == held, "Avalon command changed while waitrequest was asserted"
            select, address, read_n, write_n, data = pins
            wait = self.stall or self.cycle % 3 == 0
            available = bool(self.rx)
            reply = ((1 << 15) | self.rx[0]) if available else 0
            if address:
                reply = (64 << 16) if self.space else 0
            self.dut.m_waitrequest.value = int(wait)
            self.dut.m_readdata.value = reply
            held = pins if select and wait and not reset else None
            await RisingEdge(self.dut.clk)
            if reset:
                self.rx.clear()
                self.tx.clear()
            elif select and not wait:
                assert read_n != write_n, "simultaneous read/write or empty transaction"
                if not read_n and not address and available:
                    self.rx.popleft()
                if not write_n:
                    assert address == 0 and self.space
                    assert data <= 255
                    self.tx.append(data)

    async def cycles(self, count):
        for _ in range(count):
            await RisingEdge(self.dut.clk)
        await Timer(1, units="ns")

    async def reset(self):
        self.dut.rst_n.value = 0
        await self.cycles(5)
        self.dut.rst_n.value = 1
        await self.cycles(5)

    async def send(self, frame, gap=0):
        for byte in frame:
            self.rx.append(byte)
            if gap:
                await self.cycles(gap)

    async def write(self, address, value, gap=0):
        await self.send(b"W" + struct.pack(">II", address, value), gap)
        for _ in range(1000):
            if not self.rx:
                return
            await self.cycles(1)
        raise AssertionError("emulated UART did not consume the write frame")

    async def read(self, address, gap=0):
        await self.send(b"R" + struct.pack(">I", address), gap)
        for _ in range(1000):
            if len(self.tx) >= 4:
                return int.from_bytes(bytes(self.tx.popleft() for _ in range(4)), "big")
            await self.cycles(1)
        raise AssertionError("RTL did not produce a complete response")


async def setup(dut):
    dut.rst_n.value = 0
    dut.m_waitrequest.value = 1
    dut.m_readdata.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    uart = UartSlave(dut)
    cocotb.start_soon(uart.run())
    await uart.reset()
    return uart


@cocotb.test()
async def test_shared_counter_sequence(dut):
    uart = await setup(dut)
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    seed = int(os.environ.get("TB_SEED", "1234"))
    log = TransactionLog()
    for operation, address, value in commands(cycles, seed):
        if operation == "write":
            await uart.write(address, value, gap=2)
        else:
            value = await uart.read(address, gap=3)
        log.record(operation, address, value)
    check_log(log, cycles, seed)
    before = await uart.read(COUNT)
    await uart.cycles(100)
    assert await uart.read(COUNT) == before, "counter ran freely between host steps"
    assert not int(dut.fault.value)
    trace = Path(os.environ.get("JTAG_TRACE", "results/jtag-counter-sim.json"))
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text(json.dumps({
        "schema": 1, "kind": "jtag_counter_simulation",
        "simulator": cocotb.SIM_NAME, "cycles": cycles, "seed": seed,
        "source_format": SOURCE_FORMAT,
        "sources": source_hashes(), "transactions": [asdict(item) for item in log],
    }, indent=2) + "\n", encoding="utf-8")


@cocotb.test()
async def test_wrap_reset_and_exact_step_count(dut):
    uart = await setup(dut)
    for _ in range(260):
        await uart.write(STEP, 1)
    assert await uart.read(COUNT) == 4
    assert await uart.read(STEPS) == 260
    await uart.write(STEP, 0)
    assert await uart.read(COUNT) == 4
    assert await uart.read(STEPS) == 261
    await uart.write(RESET, 1)
    assert await uart.read(COUNT) == 0
    assert await uart.read(STEPS) == 0


@cocotb.test()
async def test_backpressure_and_partial_frame_reset(dut):
    uart = await setup(dut)
    uart.space = False
    await uart.send(b"R" + struct.pack(">I", ID))
    await uart.cycles(160)
    assert not uart.tx and not int(dut.fault.value)
    uart.space = True
    for _ in range(200):
        if len(uart.tx) == 4:
            break
        await uart.cycles(1)
    assert bytes(uart.tx) == IDENTITY.to_bytes(4, "big")
    uart.tx.clear()
    await uart.send(b"W\x00\x00", gap=20)
    await uart.reset()
    assert await uart.read(ID) == IDENTITY
    assert await uart.read(COUNT) == 0


@cocotb.test()
async def test_stalls_fail_closed_until_reset(dut):
    uart = await setup(dut)
    for mode in ("partial", "bus", "tx"):
        if mode == "partial":
            await uart.send(b"W\x00")
        elif mode == "bus":
            uart.stall = True
        else:
            uart.space = False
            await uart.send(b"R" + struct.pack(">I", ID))
        await uart.cycles(800)
        assert int(dut.fault.value), f"{mode} did not time out"
        uart.stall = False
        uart.space = True
        await uart.send(b"W" + struct.pack(">II", STEP, 1))
        await uart.cycles(100)
        assert int(dut.count.value) == 0 and not uart.tx
        await uart.reset()
        assert await uart.read(ID) == IDENTITY


@cocotb.test()
async def test_bad_commands_addresses_and_data_never_alias(dut):
    uart = await setup(dut)
    frames = (
        b"X", b"R" + struct.pack(">I", 0x10000003),
        b"W" + struct.pack(">II", 0x10000002, 1),
        b"W" + struct.pack(">II", STEP, 0x57525752),
        b"W" + struct.pack(">II", COUNT, 1),
    )
    for frame in frames:
        await uart.send(frame, gap=5)
        await uart.cycles(180)
        assert int(dut.fault.value)
        assert int(dut.count.value) == 0 and not uart.tx
        await uart.reset()
        assert await uart.read(ID) == IDENTITY
