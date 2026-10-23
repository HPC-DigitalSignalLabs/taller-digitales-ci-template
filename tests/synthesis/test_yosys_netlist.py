# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Tests for the structural Yosys netlist gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from check_yosys_netlist import validate_netlist  # noqa: E402


def test_accepts_a_mapped_cyclone_v_netlist(tmp_path: Path) -> None:
    path = tmp_path / "netlist.json"
    path.write_text(
        json.dumps(
            {
                "modules": {
                    "skid_buffer": {
                        "cells": {"cell": {"type": "MISTRAL_ALUT6"}}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    validate_netlist(path, "skid_buffer")


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"modules": {"skid_buffer": {"cells": {}}}},
        {
            "modules": {
                "skid_buffer": {"cells": {"cell": {"type": "$_DFF_P_"}}}
            }
        },
    ],
)
def test_rejects_missing_or_unmapped_logic(
    tmp_path: Path,
    document: dict,
) -> None:
    path = tmp_path / "netlist.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_netlist(path, "skid_buffer")
