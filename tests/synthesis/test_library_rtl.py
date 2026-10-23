# SPDX-License-Identifier: Apache-2.0
"""Real tool checks for the new E1 families; generated evidence stays isolated."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.simulation
@pytest.mark.parametrize("design,width", [
    ("control_fsm", 8),
    ("enabled_register", 1), ("enabled_register", 8), ("enabled_register", 32),
    ("bin2bcd", 1), ("bin2bcd", 8), ("bin2bcd", 32),
    ("bcd_to_7seg", 8),
])
def test_cyclone_v_mapping(design: str, width: int, tmp_path: Path) -> None:
    if not shutil.which("yosys"):
        pytest.skip("Yosys is not installed")
    proc = subprocess.run(
        ["make", "synth", f"DESIGN={design}", f"WIDTH={width}",
         f"RESULTS_DIR={tmp_path}"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-5000:] + proc.stderr[-3000:]
    module = json.loads((tmp_path / f"yosys-{design}-w{width}.json").read_text())[
        "modules"][design]
    types = {cell["type"] for cell in module["cells"].values()}
    assert any(kind.startswith("MISTRAL_") for kind in types)
    assert not any("latch" in kind.lower() for kind in types)
    if design == "enabled_register":
        assert len(module["ports"]["data_in"]["bits"]) == width
        assert len(module["ports"]["data_out"]["bits"]) == width
    if design == "bin2bcd":
        assert len(module["ports"]["binary"]["bits"]) == width
        assert len(module["ports"]["bcd"]["bits"]) == 4 * ((width + 2) // 3)


@pytest.mark.simulation
@pytest.mark.parametrize("design,width", [
    ("enabled_register", 0), ("bin2bcd", 0), ("bin2bcd", 65),
])
def test_invalid_parameter_is_not_silently_accepted(
    design: str, width: int, tmp_path: Path,
) -> None:
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus is not installed")
    executable = tmp_path / "invalid.vvp"
    proc = subprocess.run(
        ["iverilog", "-g2012", "-s", design, f"-P{design}.WIDTH={width}",
         "-o", str(executable), f"lib/{design}/{design}.sv"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    # A compilation rejection is valid; otherwise the parameter guard must
    # fail at time zero. Merely emitting a width warning is not sufficient.
    if proc.returncode == 0:
        proc = subprocess.run(["vvp", str(executable)], capture_output=True,
                              text=True, timeout=30)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "WIDTH" in proc.stdout + proc.stderr
