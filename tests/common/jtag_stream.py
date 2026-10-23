# SPDX-License-Identifier: Apache-2.0
"""Count-preserving JTAG Atlantic I/O, isolated from native driver hangs."""

from __future__ import annotations

import ctypes
from importlib.metadata import version
import inspect
import math
import multiprocessing
from multiprocessing.connection import Connection
import time
from typing import Any

from transport import TransportError


def driver_api() -> tuple[Any, dict[str, Any]]:
    """Resolve the supported binding without opening a cable."""
    if version("intel-jtag-uart") != "0.0.3":
        raise TransportError("the station requires intel-jtag-uart==0.0.3")
    import intel_jtag_uart

    driver = intel_jtag_uart.intel_jtag_uart
    inspect.signature(driver).bind(cable_name=None, device_nr=-1, instance_nr=0)
    signatures = {
        "open": (ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]),
        "read": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]),
        "write": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]),
        "flush": (ctypes.c_int, [ctypes.c_void_p]),
        "close": (None, [ctypes.c_void_p]),
    }
    functions = {}
    for name, (restype, argtypes) in signatures.items():
        function = driver.function_table[f"jtagatlantic_{name}"]["ptr"]
        # The upstream wrapper discards write/flush counts and uses C long for
        # C int. Normalize in this isolated process, including on 64-bit Linux.
        function.restype = restype
        function.argtypes = argtypes
        functions[name] = function
    return driver, functions


class NativeJtagStream:
    """Internal worker backend. The parent bounds every native operation."""

    def __init__(self, stream: Any, functions: dict[str, Any]) -> None:
        self.stream = stream
        self.functions = functions

    def _transfer(self, operation: str, buffer: Any, count: int) -> int:
        while True:
            transferred = self.functions[operation](self.stream.handle, buffer, count)
            if type(transferred) is not int or not 0 <= transferred <= count:
                raise TransportError(f"JTAG {operation} returned invalid count {transferred!r}")
            if transferred:
                return transferred
            time.sleep(0.001)

    def write(self, data: bytes) -> int:
        buffer = ctypes.create_string_buffer(data)
        count = self._transfer("write", buffer, len(data))
        if self.functions["flush"](self.stream.handle) < 0:
            raise TransportError("JTAG flush failed")
        return count

    def read(self, count: int) -> bytes:
        buffer = ctypes.create_string_buffer(count)
        received = self._transfer("read", buffer, count)
        return buffer.raw[:received]

    def close(self) -> None:
        self.functions["close"](self.stream.handle)


def _jtag_worker(connection: Connection, cable: str | None, device: int, instance: int) -> None:
    native = None
    try:
        driver, functions = driver_api()
        native = NativeJtagStream(
            driver(cable_name=cable, device_nr=device, instance_nr=instance), functions,
        )
        connection.send((True, None))
        while True:
            operation, argument = connection.recv()
            if operation == "close":
                native.close()
                native = None
                connection.send((True, None))
                return
            if operation not in ("read", "write"):
                raise TransportError(f"unknown JTAG operation: {operation}")
            result = getattr(native, operation)(argument)
            connection.send((True, result))
    except EOFError:
        return
    except Exception as exc:
        # Serialize failures across the process boundary; never return data on
        # a failed operation. The parent poisons the whole transport.
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        if native is not None:
            native.close()
        connection.close()


class JtagStream:
    """One cable session in a killable worker, with a per-operation deadline."""

    def __init__(
        self, cable: str | None, device: int, instance: int, timeout: float,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("JTAG timeout must be finite and positive")
        self.timeout = timeout
        self._closed = False
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(
            target=_jtag_worker, args=(child, cable, device, instance), daemon=True,
        )
        try:
            self._process.start()
        except Exception:
            self._connection.close()
            child.close()
            raise
        child.close()
        self._receive("open")

    def _stop(self) -> None:
        self._closed = True
        self._connection.close()
        self._process.join(timeout=0.1)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1)
        if self._process.is_alive():
            self._process.kill()
            self._process.join()
        self._process.close()

    def _receive(self, operation: str) -> Any:
        try:
            if not self._connection.poll(self.timeout):
                raise TransportError(
                    f"JTAG {operation} timed out after {self.timeout:g}s; "
                    "reset the device decoder and reopen the channel"
                )
            success, result = self._connection.recv()
            if not success:
                raise TransportError(f"JTAG {operation} failed: {result}")
            return result
        except (EOFError, OSError, TransportError) as exc:
            self._stop()
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"JTAG worker disconnected during {operation}: {exc}") from exc

    def _request(self, operation: str, argument: Any = None) -> Any:
        if self._closed:
            raise TransportError("JTAG session is closed; reset the decoder before reopening")
        try:
            self._connection.send((operation, argument))
        except (OSError, EOFError) as exc:
            self._stop()
            raise TransportError(f"JTAG worker disconnected: {exc}") from exc
        return self._receive(operation)

    def write(self, data: bytes) -> int:
        return self._request("write", data)

    def read(self, count: int) -> bytes:
        return self._request("read", count)

    def close(self) -> None:
        if not self._closed:
            self._request("close")
            self._stop()
