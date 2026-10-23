# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Validate the structural output of the open Cyclone V synthesis check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_netlist(path: Path, top: str) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing Yosys netlist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Yosys JSON: {path}: {exc}") from exc

    module = document.get("modules", {}).get(top)
    if module is None:
        raise ValueError(f"top module {top!r} is absent from {path}")

    cells = module.get("cells", {})
    if not cells:
        raise ValueError(f"top module {top!r} contains no synthesized cells")

    cell_types = {cell.get("type", "") for cell in cells.values()}
    if not any(cell_type.startswith("MISTRAL_") for cell_type in cell_types):
        raise ValueError(
            "netlist has no MISTRAL cells; synth_intel_alm did not map "
            "the design for Cyclone V"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("netlist", type=Path)
    parser.add_argument("--top", required=True)
    args = parser.parse_args()
    try:
        validate_netlist(args.netlist, args.top)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"validated Cyclone V logical netlist: {args.netlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
