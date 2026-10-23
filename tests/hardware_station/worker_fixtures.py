# SPDX-License-Identifier: Apache-2.0
"""Spawn targets with standard-library imports only; never a hardware driver."""

import time


def start_ready_worker(ready, target, args, bootstrap_delay=0):
    time.sleep(bootstrap_delay)
    ready.set()
    target(*args)


def fake_worker(connection, cable, device, instance):
    connection.send((True, (cable, device, instance)))
    while True:
        operation, value = connection.recv()
        if operation == "close":
            connection.send((True, None))
            connection.close()
            return
        if operation == "write":
            connection.send((True, len(value)))
        elif value == 99:
            time.sleep(60)
        elif value == 98:
            connection.send((False, "cable disconnected"))
            return
        else:
            connection.send((True, b"\x00" * value))


def stalled_open(connection, cable, device, instance):
    time.sleep(60)
