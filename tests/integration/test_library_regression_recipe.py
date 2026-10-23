# SPDX-License-Identifier: Apache-2.0
"""Execute the library recipe with controlled tools, never reusing old evidence."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def regression_recipe() -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("\nlibrary-regression: | $(RESULTS_DIR)\n", 1)[1]
    recipe = recipe.split("\ntest:", 1)[0].strip()
    recipe = "\n".join(line.lstrip("\t").removeprefix("@")
                       for line in recipe.splitlines())
    recipe = recipe.replace("$(RESULTS_DIR)", "results")
    recipe = recipe.replace("$(PYTHON)", "fake_python")
    recipe = recipe.replace("$(LIBRARY_TEST_DIRS)", "tests/control_fsm")
    assert "$(" not in recipe, "Update the harness when Make variables change"
    return recipe


@pytest.mark.parametrize("verilator,icarus,pytest_status,evidence,expected", [
    (False, True, 0, False, 2),
    (True, False, 0, False, 2),
    (False, False, 0, False, 2),
    (True, True, 4, False, 4),   # collection/command-line failure, no new XML
    (True, True, 1, True, 1),    # failed tests may publish fresh failure XML
    (True, True, 0, True, 0),
])
def test_library_regression_removes_stale_evidence_before_preflight(
    tmp_path: Path, verilator: bool, icarus: bool,
    pytest_status: int, evidence: bool, expected: int,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to exercise the recipe")
    results = tmp_path / "results"
    results.mkdir()
    for name in ("junit-library.xml", "library.html"):
        (results / name).write_text("stale evidence", encoding="utf-8")
    produce = ('printf fresh > results/junit-library.xml; '
               'printf fresh > results/library.html;' if evidence else ":;")
    # Make normally stops after any failed recipe line. set -e models that
    # behavior when the individual recipe lines are executed in one shell.
    harness = f"""
set -e
command() {{
    case "$*" in
        "-v verilator") return {0 if verilator else 1} ;;
        "-v iverilog") return {0 if icarus else 1} ;;
        *) return 99 ;;
    esac
}}
fake_python() {{
    test ! -e results/junit-library.xml
    test ! -e results/library.html
    case "$*" in
        *"--junitxml=results/junit-library.xml"*) : ;;
        *) return 98 ;;
    esac
    touch pytest-invoked
    {produce}
    return {pytest_status}
}}
"""
    proc = subprocess.run([bash, "-c", harness + regression_recipe()],
                          cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert proc.returncode == expected, proc.stdout + proc.stderr
    assert (tmp_path / "pytest-invoked").exists() == (verilator and icarus)
    for name in ("junit-library.xml", "library.html"):
        path = results / name
        if evidence and verilator and icarus:
            assert path.read_text(encoding="utf-8") == "fresh"
        else:
            assert not path.exists()
