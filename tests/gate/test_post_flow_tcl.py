# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Regression tests for ``quartus/post_flow.tcl``.

``post_flow.tcl`` is the bridge between the proprietary domain (Quartus, which
cannot run in CI because its licence is node-locked) and the open one. It is
therefore the half of the gate that CI can never re-derive, and three of the
eight defects found by adversarial review lived here -- yet nothing covered it,
because running it appeared to require Quartus.

It does not. The script touches only four Quartus APIs, so a small Tcl harness
stubs them and the script runs under plain ``tclsh`` against a real throwaway
git repository. What is exercised is the git plumbing and the provenance
record, which is exactly where the defects were.

The end-to-end agreement with the consumer side is asserted too: every blob id
in the sidecar must equal what ``git hash-object`` produces, since the design
rule of the script is that it computes no hash itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from provenance import SCHEMA_VERSION, SIDECAR_SUFFIX  # noqa: E402

POST_FLOW = Path(__file__).resolve().parents[2] / "quartus" / "post_flow.tcl"

pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None, reason="tclsh is not installed"
)

# Stubs for the only Quartus APIs the script uses. `post_message` writes to
# stderr on purpose: it is how the script reports, and it must not be mistaken
# for failure.
HARNESS = """\
namespace eval ::quartus {}
set ::quartus(version) "Quartus Prime Lite 18.1"
proc ::get_current_revision {} { return "adder" }
proc ::get_global_assignment {args} {
    if {[lindex $args 1] eq "DEVICE"} { return "5CSEBA6U23I7" }
    return "Cyclone V"
}
proc ::post_message {args} { puts stderr "\\[quartus\\] [lindex $args end]" }
package provide ::quartus::flow 1.0
source [lindex $argv 0]
"""

REPORT = "Flow Status ; Successful - Wed Aug 12 00:00:00 2026\n"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repository laid out the way the template expects."""
    repo = tmp_path / "proj"
    (repo / "src").mkdir(parents=True)
    (repo / "quartus").mkdir()
    (repo / "output_files").mkdir()
    (repo / "src" / "adder.sv").write_text("module adder; endmodule\n")
    (repo / "quartus" / "adder.qsf").write_text("set_global_assignment -name X Y\n")

    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _run(repo: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    (repo / "output_files" / "adder.flow.rpt").write_text(REPORT)
    harness = repo / "harness.tcl"
    harness.write_text(HARNESS)
    return subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(cwd or repo),
        capture_output=True,
        text=True,
    )


def _sidecar(repo: Path) -> dict:
    path = repo / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}"
    return json.loads(path.read_text())


def test_the_script_runs_and_emits_a_wellformed_sidecar(project: Path) -> None:
    proc = _run(project)
    assert proc.returncode == 0, proc.stderr

    prov = _sidecar(project)
    assert prov["schema"] == SCHEMA_VERSION
    assert prov["commit"] == _git(project, "rev-parse", "HEAD")
    assert prov["dirty"] is False
    assert prov["tool"]["device"] == "5CSEBA6U23I7"


def test_every_blob_id_agrees_with_git(project: Path) -> None:
    """The script must compute no hash of its own; git is the single authority."""
    _run(project)
    prov = _sidecar(project)

    assert prov["sources"], "a sidecar with no sources would make coverage vacuous"
    for rel, blob in prov["sources"].items():
        assert blob == _git(project, "hash-object", "--", str(project / rel)), rel

    report = project / prov["report"]["path"]
    assert prov["report"]["blob"] == _git(project, "hash-object", "--", str(report))


def test_source_paths_are_repository_relative(project: Path) -> None:
    """Regression: paths must be root-relative, or the gate cannot resolve them.

    ``git ls-tree`` prints paths relative to the *current directory* while
    ``git rev-parse <rev>:<path>`` always resolves from the repository root.
    Mixing the two made every source look absent when the script ran from a
    subdirectory.
    """
    _run(project)
    for rel in _sidecar(project)["sources"]:
        assert not Path(rel).is_absolute()
        assert (project / rel).exists(), f"{rel} does not resolve from the root"
    assert "src/adder.sv" in _sidecar(project)["sources"]


def test_running_from_a_subdirectory_finds_the_same_sources(project: Path) -> None:
    """Regression: Quartus is normally invoked from quartus/, not the root."""
    proc = _run(project, cwd=project / "quartus")
    assert proc.returncode == 0, proc.stderr

    prov = _sidecar(project)
    assert "src/adder.sv" in prov["sources"], prov["sources"]
    assert "quartus/adder.qsf" in prov["sources"], prov["sources"]


def test_a_modified_source_is_reported_dirty(project: Path) -> None:
    """A dirty tree must be reported as dirty. The gate then rejects it."""
    (project / "src" / "adder.sv").write_text("module adder; wire broken; endmodule\n")
    proc = _run(project)
    assert proc.returncode == 0, proc.stderr
    assert _sidecar(project)["dirty"] is True


def test_a_git_advisory_on_stderr_does_not_declare_a_dirty_tree_clean(
    project: Path,
) -> None:
    """Regression for the fail-open defect, forced deterministically.

    Tcl's ``exec`` raises whenever the child writes to *stderr*, even on exit 0,
    and the original helper turned any error into ``""`` -- which is exactly
    what a *clean* tree returns. So a git advisory silently produced
    ``"dirty": false`` on a dirty tree: a gate that fails **open**.

    Whether real git emits an advisory depends on its version and config (an
    attempt to trigger the CRLF warning did not reproduce it here), so this test
    does not rely on that. It puts a shim named ``git`` first on PATH which
    writes one line to stderr and then delegates to the real git, exiting with
    its status. That is the hazard in its pure form.
    """
    import os

    (project / "src" / "adder.sv").write_text("module adder; wire broken; endmodule\n")

    shim_dir = project / "shim"
    shim_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git, "git is required for these tests"
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'echo "warning: an advisory on stderr" >&2\n'
        f'exec {real_git} "$@"\n'
    )
    shim.chmod(0o755)

    (project / "output_files" / "adder.flow.rpt").write_text(REPORT)
    harness = project / "harness.tcl"
    harness.write_text(HARNESS)
    subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(project),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
    )

    # The safety property, stated as the disjunction it really is: either the
    # script refuses to vouch at all (no sidecar -> the gate rejects on a
    # missing artifact), or it vouches and says the tree is dirty. What must
    # never happen is a sidecar claiming "clean" for a tree that is not.
    sidecar = project / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}"
    vouched_clean = sidecar.exists() and _sidecar(project)["dirty"] is False
    assert not vouched_clean, (
        "a stderr advisory made a dirty tree look clean: the gate fails open"
    )


def test_rewriting_the_report_alone_does_not_mark_the_tree_dirty(project: Path) -> None:
    """Regression: the dirty probe once covered output_files/ too.

    The script writes the report itself, so an unscoped probe made every compile
    after the first report itself as a modification, and the gate rejected
    honest artifacts.
    """
    _run(project)
    assert _sidecar(project)["dirty"] is False

    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "commit the artifact")

    proc = _run(project)  # recompile: rewrites the report, touches no source
    assert proc.returncode == 0, proc.stderr
    assert _sidecar(project)["dirty"] is False


def test_a_stale_report_is_refused(project: Path) -> None:
    """Regression: running the script without compiling bound an old report.

    The candidate search once preferred the archived copy, so re-running the
    script alone would attach a previous run's report to the current sources.
    An mtime guard now rejects it.

    The asserted property is that **no fresh sidecar is produced**, not the exit
    code: the script uses ``return`` on its error paths, so it exits 0 even when
    it refuses. See ``test_error_paths_exit_zero_but_emit_nothing``.
    """
    import os

    _run(project)
    sidecar = project / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}"
    sidecar.unlink()

    old = project / "output_files" / "adder.flow.rpt"
    stale = 1_700_000_000
    os.utime(old, (stale, stale))

    harness = project / "harness.tcl"
    harness.write_text(HARNESS)
    proc = subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(project),
        capture_output=True,
        text=True,
        env={**os.environ, "POST_FLOW_MAX_REPORT_AGE_S": "60"},
    )
    assert "old" in (proc.stderr + proc.stdout).lower()
    assert not sidecar.exists(), "a refused run must not vouch for the report"

def test_error_paths_exit_zero_but_emit_nothing(project: Path) -> None:
    """Documents a limitation, so that it cannot regress silently.

    ``post_flow.tcl`` bails out with ``return``, which under ``tclsh`` (and any
    ``quartus_sh -t`` wrapper) yields **exit status 0**. A Makefile calling it
    would therefore believe it succeeded.

    That is tolerable only because the safety property does not depend on the
    exit code: on every refusal the script emits no sidecar, and the CI gate
    treats a missing sidecar as a failure (dry-run scenario 3). The gate is
    still closed. If the exit code is ever made non-zero, this test should be
    updated, not deleted -- the *emit nothing* half must always hold.
    """
    import os

    _run(project)
    sidecar = project / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}"
    sidecar.unlink()

    report = project / "output_files" / "adder.flow.rpt"
    os.utime(report, (1_700_000_000, 1_700_000_000))
    harness = project / "harness.tcl"
    harness.write_text(HARNESS)
    proc = subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(project),
        capture_output=True,
        text=True,
        env={**os.environ, "POST_FLOW_MAX_REPORT_AGE_S": "60"},
    )
    assert proc.returncode == 0, "known limitation; see the docstring"
    assert not sidecar.exists(), "the safety property that compensates for it"


def test_a_project_with_no_tracked_sources_is_refused(tmp_path: Path) -> None:
    """Regression: an empty source set would make coverage vacuous.

    If no design source is tracked -- a mistyped glob, or sources never added to
    git -- then "every source blob matches" is trivially true and the gate's
    staleness check defends nothing. Absence of sources must abort, not produce
    a sidecar with an empty ``sources`` map.
    """
    repo = tmp_path / "empty"
    (repo / "output_files").mkdir(parents=True)
    (repo / "notes.md").write_text("no design sources here\n")

    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no sources")

    (repo / "output_files" / "adder.flow.rpt").write_text(REPORT)
    harness = repo / "harness.tcl"
    harness.write_text(HARNESS)
    subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert not (repo / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}").exists()


def test_outside_a_git_repository_nothing_is_vouched_for(tmp_path: Path) -> None:
    """Absence of git must never yield a sidecar: the gate then fails closed.

    The assertion is on the artifact, not on the message or the exit code:
    wording changes are not defects, and vouching without provenance is.
    """
    import os

    loose = tmp_path / "loose"
    (loose / "output_files").mkdir(parents=True)
    (loose / "output_files" / "adder.flow.rpt").write_text(REPORT)
    harness = loose / "harness.tcl"
    harness.write_text(HARNESS)

    subprocess.run(
        ["tclsh", str(harness), str(POST_FLOW)],
        cwd=str(loose),
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
    )
    assert not (loose / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}").exists()
