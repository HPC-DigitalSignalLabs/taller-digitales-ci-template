"""Exercise the simulation recipe's exit status without an HDL toolchain."""

from __future__ import annotations

import os
import shutil
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def bash_path() -> str | None:
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


def simulation_recipe() -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("\nsim:\n", 1)[1].split("\n# --- Open", 1)[0]
    recipe = recipe.strip().removeprefix("@")
    replacements = {
        '$(if $(COCOTB_RESULTS_FILE),COCOTB_RESULTS_FILE="$(COCOTB_RESULTS_FILE)")':
            'COCOTB_RESULTS_FILE="current.xml"',
        "$(MAKE)": "fake_make",
        "$(TEMPLATE_ROOT)": ".",
        "$(DESIGN)": "counter",
        "$(TOPLEVEL)": "counter",
        "$(SIM_TOPLEVEL)": "counter",
        "$(MODULE)": "tb_counter",
        "$(SIM)": "verilator",
        "$(WIDTH)": "8",
        "$(SIM_BUILD)": "sim_build",
        "$(COCOTB_RESULTS_FILE)": "current.xml",
        "$(SIM_REPORT)": "report.md",
        "$(SIM_VERILOG_SOURCES)": "counter.sv",
    }
    for key, value in replacements.items():
        recipe = recipe.replace(key, value)
    assert "$(" not in recipe, "Update the recipe harness for new Make variables"
    return recipe.replace("$$", "$")


@pytest.mark.parametrize(
    ("prerequisite", "simulator", "evidence", "reporter", "expected"),
    [
        (17, 0, "valid", 0, 17),
        (0, 23, "absent", 0, 23),
        (0, 23, "valid", 5, 23),
        (0, 0, "absent", 0, 2),
        (0, 0, "empty", 0, 2),
        (0, 0, "valid", 5, 5),
        (0, 0, "valid", 0, 0),
    ],
)
def test_simulation_recipe_propagates_failure(
    tmp_path: Path,
    prerequisite: int,
    simulator: int,
    evidence: str,
    reporter: int,
    expected: int,
) -> None:
    bash = bash_path()
    if bash is None:
        pytest.skip("Bash is required to exercise the actual recipe")
    (tmp_path / "current.xml").write_text("stale evidence", encoding="utf-8")
    (tmp_path / "report.md").write_text("stale report", encoding="utf-8")
    produce = {
        "absent": ":",
        "empty": ": > current.xml",
        "valid": "printf '<testsuite tests=\"1\" failures=\"0\"/>' > current.xml",
    }[evidence]
    harness = f"""
fake_make() {{
    case "$*" in
        *check-sim-prereqs*) return {prerequisite} ;;
        *"sim.mk sim "*) {produce}; return {simulator} ;;
        *) echo "Missing explicit simulation target" >&2; return 99 ;;
    esac
}}
python3() {{
    case "$*" in
        *--require-pass*) return {reporter} ;;
        *) echo "Missing XML verdict gate" >&2; return 99 ;;
    esac
}}
"""
    result = subprocess.run(
        [bash, "-c", harness + simulation_recipe()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert not (tmp_path / "report.md").exists()
    if prerequisite or evidence == "absent":
        assert not (tmp_path / "current.xml").exists()


def test_direct_sim_makefile_defaults_to_simulation() -> None:
    text = (ROOT / "sim.mk").read_text(encoding="utf-8")
    assert ".DEFAULT_GOAL := sim" in text


def test_simulator_exit_zero_cannot_hide_failed_cocotb_xml(tmp_path):
    bash = bash_path()
    if bash is None:
        pytest.skip("Bash is required to exercise the actual recipe")
    (tmp_path / "tools").mkdir()
    shutil.copy2(ROOT / "tools" / "human_report.py", tmp_path / "tools" / "human_report.py")
    python = shlex.quote(Path(sys.executable).as_posix())
    harness = f"""
fake_make() {{
    case "$*" in
        *check-sim-prereqs*) return 0 ;;
        *) printf '<testsuite><testcase><failure message="injected"/></testcase></testsuite>' > current.xml ;;
    esac
    return 0
}}
python3() {{ {python} "$@"; }}
"""
    result = subprocess.run(
        [bash, "-c", harness + simulation_recipe()],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "`FAIL`" in (tmp_path / "report.md").read_text(encoding="utf-8")
