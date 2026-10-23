# SPDX-License-Identifier: Apache-2.0
"""Native API and deadline tests with no cable access."""

import ctypes
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jtag_stream
from hardware import JtagUartTransport
from transport import TransportError
from worker_fixtures import fake_worker, stalled_open, start_ready_worker


@pytest.fixture
def ready_worker_context(monkeypatch):
    """Separate fixture process bootstrap from the driver operation deadline.

    A spawn child must import its target before it can emulate a native open.
    On Docker Windows mounts, importing this entire pytest module can consume
    the 3-second driver deadline. Use lightweight targets and an
    explicit readiness event. Production still uses unmodified spawn/deadlines.
    """
    context = jtag_stream.multiprocessing.get_context("spawn")
    timing = {"startup_s": 0.0, "bootstrap_delay": 0.0}

    class ReadyProcess:
        def __init__(self, *, target, args, daemon):
            self.ready = context.Event()
            self.process = context.Process(
                target=start_ready_worker,
                args=(self.ready, target, args, timing["bootstrap_delay"]), daemon=daemon,
            )

        def start(self):
            started = time.monotonic()
            self.process.start()
            if not self.ready.wait(10):
                self.process.terminate()
                self.process.join(timeout=1)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join()
                self.process.close()
                pytest.fail("test worker could not bootstrap; no driver operation was attempted")
            timing["startup_s"] += time.monotonic() - started

        def __getattr__(self, name):
            return getattr(self.process, name)

    def get_context(method):
        assert method == "spawn"
        return SimpleNamespace(Pipe=context.Pipe, Process=ReadyProcess)

    monkeypatch.setattr(jtag_stream.multiprocessing, "get_context", get_context)
    return timing


def test_driver_api_uses_real_keywords_and_c_int(monkeypatch):
    class Function:
        pass
    class Driver:
        function_table = {
            f"jtagatlantic_{name}": {"ptr": Function()}
            for name in ("open", "read", "write", "flush", "close")
        }
        def __init__(self, cable_name=None, device_nr=-1, instance_nr=-1):
            pytest.fail("API inspection must not open a cable")
    monkeypatch.setattr(jtag_stream, "version", lambda name: "0.0.3")
    monkeypatch.setitem(sys.modules, "intel_jtag_uart", SimpleNamespace(intel_jtag_uart=Driver))
    driver, functions = jtag_stream.driver_api()
    assert driver is Driver
    assert functions["read"].argtypes == [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    assert functions["write"].restype is ctypes.c_int
    assert functions["flush"].restype is ctypes.c_int
    assert functions["open"].argtypes[1:3] == [ctypes.c_int, ctypes.c_int]


def test_unknown_driver_version_is_not_silently_used(monkeypatch):
    monkeypatch.setattr(jtag_stream, "version", lambda name: "1.0")
    with pytest.raises(TransportError, match="0.0.3"):
        jtag_stream.driver_api()


def test_native_read_never_pads_short_reads():
    def read(handle, buffer, count):
        assert handle == 42 and count == 4
        ctypes.memmove(buffer, b"\x12\x34", 2)
        return 2
    native = jtag_stream.NativeJtagStream(SimpleNamespace(handle=42), {"read": read})
    assert native.read(4) == b"\x12\x34"


def test_native_write_preserves_partial_count_and_checks_flush():
    writes = []
    def write(handle, buffer, count):
        writes.append(buffer.raw[:count])
        return 2
    native = jtag_stream.NativeJtagStream(
        SimpleNamespace(handle=42), {"write": write, "flush": lambda handle: 0},
    )
    assert native.write(b"abcd") == 2
    assert writes == [b"abcd"]
    native.functions["flush"] = lambda handle: -1
    with pytest.raises(TransportError, match="flush"):
        native.write(b"abcd")


@pytest.mark.parametrize("count", [-1, 5])
def test_invalid_native_counts_fail(count):
    native = jtag_stream.NativeJtagStream(
        SimpleNamespace(handle=42), {"read": lambda *args: count},
    )
    with pytest.raises(TransportError, match="invalid count"):
        native.read(4)


def test_empty_native_read_is_polled_without_inventing_data(monkeypatch):
    responses = iter([0, 0, 1])
    sleeps = []
    def read(handle, buffer, count):
        buffer[0] = b"\x57"
        return next(responses)
    monkeypatch.setattr(jtag_stream.time, "sleep", sleeps.append)
    native = jtag_stream.NativeJtagStream(SimpleNamespace(handle=42), {"read": read})
    assert native.read(4) == b"\x57"
    assert sleeps == [0.001, 0.001]


def test_worker_transport_framing_and_idempotent_close(monkeypatch, ready_worker_context):
    monkeypatch.setattr(jtag_stream, "_jtag_worker", fake_worker)
    with JtagUartTransport(cable="DE-SoC", device=2, instance=0, timeout=3) as transport:
        transport.write(1, 1)
        assert transport.read(3) == 0
        assert len(transport.log) == 2
    transport.close()
    with pytest.raises(TransportError, match="closed"):
        transport.read(3)


def test_slow_fixture_bootstrap_is_not_a_native_open_timeout(monkeypatch, ready_worker_context):
    monkeypatch.setattr(jtag_stream, "_jtag_worker", fake_worker)
    ready_worker_context["bootstrap_delay"] = 0.3
    stream = jtag_stream.JtagStream(None, -1, 0, 0.1)
    assert ready_worker_context["startup_s"] >= 0.3
    stream.close()


def test_native_call_hang_terminates_only_its_worker(monkeypatch, ready_worker_context):
    monkeypatch.setattr(jtag_stream, "_jtag_worker", fake_worker)
    stream = jtag_stream.JtagStream(None, -1, 0, 3)
    stream.timeout = 0.05
    started = time.monotonic()
    with pytest.raises(TransportError, match="timed out"):
        stream.read(99)
    assert time.monotonic() - started < 3
    assert stream._closed
    stream.close()
    with pytest.raises(TransportError, match="closed"):
        stream.write(b"W")


def test_open_hang_is_bounded(monkeypatch, ready_worker_context):
    monkeypatch.setattr(jtag_stream, "_jtag_worker", stalled_open)
    started = time.monotonic()
    with pytest.raises(TransportError, match="open timed out"):
        jtag_stream.JtagStream(None, -1, 0, 0.1)
    assert time.monotonic() - started - ready_worker_context["startup_s"] < 3


def test_worker_failure_closes_session(monkeypatch, ready_worker_context):
    monkeypatch.setattr(jtag_stream, "_jtag_worker", fake_worker)
    stream = jtag_stream.JtagStream(None, -1, 0, 3)
    with pytest.raises(TransportError, match="cable disconnected"):
        stream.read(98)
    assert stream._closed


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_deadlines_are_rejected_before_open(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        jtag_stream.JtagStream(None, -1, 0, timeout)
