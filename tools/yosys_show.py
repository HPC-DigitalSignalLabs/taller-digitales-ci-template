# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Render a readable schematic of a student's elaborated RTL.

This tool is a *pedagogical visualisation* of the RTL, not a synthesis,
place-and-route, bitstream or timing result. It elaborates the design with
Yosys and emits a human-readable schematic so a student can SEE the logic they
described, complementing (never replacing) the ``synth_intel_alm`` gate driven
by ``tools/check_yosys_netlist.py``.

Two views coexist on purpose:

* The synthesis gate (``make synth``) maps the design to Cyclone V ``MISTRAL_``
  cells. That netlist is authoritative for the open logical-synthesis check but
  visually unreadable: ALM-mapped graphs are noise to a learner.
* This tool stops *before* technology mapping (``proc``/``opt``/``memory
  -nomap``) so the schematic still shows recognisable adders, muxes, registers
  and wires. It is the "understand my RTL" view, not the "prove it maps" view.

Quartus remains the authority for placement, routing, bitstream and STA. A
schematic produced here is never evidence of timing closure or hardware
behaviour; it only mirrors the elaborated logic.

The rendering itself needs ``yosys`` and Graphviz ``dot``, which live in the
DevContainer image, not on the developer's host. Every piece of *logic* in this
module (script and command construction, path naming, tool detection) is a pure
function so it can be unit-tested with neither tool installed. Only the runner
shells out, and it fails with an actionable message naming the missing tool.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def netlist_stem(design: str, width: int) -> str:
    """Return the shared basename for a design's schematic artifacts.

    Mirrors the ``yosys-<design>-w<width>`` convention already used by the
    synthesis gate, but prefixed with ``netlist-`` so the readable schematic
    never collides with the machine-readable JSON netlist.
    """
    return f"netlist-{design}-w{width}"


def dot_output_path(results_dir: Path, design: str, width: int) -> Path:
    """Absolute path of the Graphviz ``.dot`` file for this design/width."""
    return results_dir / f"{netlist_stem(design, width)}.dot"


def svg_output_path(results_dir: Path, design: str, width: int) -> Path:
    """Absolute path of the rendered ``.svg`` file for this design/width."""
    return results_dir / f"{netlist_stem(design, width)}.svg"


def build_yosys_script(
    sources: list[Path],
    top: str,
    width: int,
    dot_path: Path,
    *,
    apply_width: bool = True,
    rising: int | None = None,
) -> str:
    """Return the Yosys command string that emits a readable schematic.

    The pass order deliberately stops before technology mapping so the diagram
    stays legible:

    * ``read_verilog -sv`` loads every SystemVerilog source.
    * ``chparam -set WIDTH`` applies the requested parameterisation, matching
      how ``make synth`` overrides ``WIDTH`` per design.
    * ``hierarchy -top`` selects and elaborates the top module.
    * ``proc`` lowers processes to netlist primitives; ``opt`` and
      ``opt_clean`` remove clutter; ``memory -nomap`` keeps memories as
      recognisable ``$mem`` blocks instead of exploding them into gates.
    * ``show`` writes Graphviz ``dot`` using the artifact stem (without its
      extension, as Yosys appends it) and ``-notitle`` for a clean canvas.

    ``show`` receives the prefix without the ``.dot`` suffix because Yosys adds
    the extension itself.
    """
    source_list = " ".join(str(source) for source in sources)
    prefix = str(dot_path.with_suffix(""))
    commands = [f"read_verilog -sv {source_list}"]
    if apply_width:
        commands.append(f"chparam -set WIDTH {width} {top}")
    if rising is not None:
        if rising not in (0, 1):
            raise ValueError("RISING must be 0 or 1")
        commands.append(f"chparam -set RISING {rising} {top}")
    commands += [
        f"hierarchy -top {top}",
        "proc",
        "opt",
        "memory -nomap",
        "opt_clean",
        f"show -format dot -prefix {prefix} -notitle {top}",
    ]
    return "; ".join(commands)


def build_yosys_command(script: str) -> list[str]:
    """Return the ``yosys`` argument vector for the given script string."""
    return ["yosys", "-p", script]


def build_dot_command(dot_path: Path, svg_path: Path) -> list[str]:
    """Return the Graphviz ``dot`` argument vector to render ``dot`` -> SVG."""
    return ["dot", "-Tsvg", str(dot_path), "-o", str(svg_path)]


def _require_tool(tool: str) -> str:
    """Return the resolved path of ``tool`` or raise an actionable error.

    Never surfaces a raw ``FileNotFoundError``: the message names the missing
    executable and reminds the reader it ships in the DevContainer image.
    """
    resolved = shutil.which(tool)
    if resolved is None:
        raise RuntimeError(
            f"required tool {tool!r} is not on PATH; it ships in the "
            f"open-source verification DevContainer image (see "
            f".devcontainer/Dockerfile). Run this inside the container."
        )
    return resolved


def render_schematic(
    sources: list[Path],
    top: str,
    width: int,
    design: str,
    results_dir: Path,
    runner: object = subprocess.run,
    *,
    apply_width: bool = True,
    rising: int | None = None,
) -> tuple[Path, Path | None]:
    """Elaborate the RTL and render a readable schematic.

    Returns ``(dot_path, svg_path)``. ``svg_path`` is ``None`` when Graphviz
    ``dot`` is unavailable, in which case only the ``.dot`` source is produced
    and the caller is told the SVG was skipped. ``runner`` is injectable so the
    shelling-out is never exercised by unit tests.
    """
    _require_tool("yosys")
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = netlist_stem(design, width)
    if rising is not None:
        stem += f"-r{rising}"
    dot_path = results_dir / f"{stem}.dot"
    svg_path = results_dir / f"{stem}.svg"

    script = build_yosys_script(
        sources, top, width, dot_path, apply_width=apply_width, rising=rising
    )
    runner(build_yosys_command(script), check=True)

    if shutil.which("dot") is None:
        return dot_path, None
    runner(build_dot_command(dot_path, svg_path), check=True)
    return dot_path, svg_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, help="design name for artifact naming")
    parser.add_argument("--top", required=True, help="top module to elaborate and show")
    parser.add_argument("--width", type=int, default=8, help="WIDTH parameter override")
    parser.add_argument("--no-width", action="store_true",
                        help="top has no WIDTH parameter (single-bit blocks)")
    parser.add_argument("--rising", type=int, choices=(0, 1),
                        help="edge_detector polarity; separate artifact suffix")
    parser.add_argument(
        "--sources",
        required=True,
        nargs="+",
        type=Path,
        help="SystemVerilog source files",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="directory that receives the .dot and .svg artifacts",
    )
    args = parser.parse_args()

    try:
        dot_path, svg_path = render_schematic(
            sources=args.sources,
            top=args.top,
            width=args.width,
            design=args.design,
            results_dir=args.results_dir,
            apply_width=not args.no_width,
            rising=args.rising,
        )
    except RuntimeError as exc:
        parser.exit(status=1, message=f"error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(status=exc.returncode or 1, message=f"error: schematic tool failed: {exc}\n")

    print(f"readable RTL schematic (Graphviz dot): {dot_path}")
    if svg_path is None:
        print(
            "note: Graphviz 'dot' not found; wrote the .dot source only. "
            "Install graphviz (present in the DevContainer) to render the SVG."
        )
    else:
        print(f"rendered schematic (SVG): {svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
