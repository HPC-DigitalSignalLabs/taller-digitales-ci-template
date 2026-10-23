# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Dry-run scenarios for the synthesis-artifact gate (OE4).

Each test builds a real throwaway git repository and drives the gate against it,
so the git plumbing is exercised rather than mocked. The scenario numbers refer
to the six controlled scenarios defined in the anteproyecto; the three that do
not need an FPGA attached are covered here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from artifact_gate import (  # noqa: E402
    DirtyTree,
    GateInternalError,
    MalformedProvenance,
    MissingArtifact,
    StaleArtifact,
    SynthesisFailed,
    TamperedReport,
    validate,
)
from provenance import SCHEMA_VERSION, SIDECAR_SUFFIX  # noqa: E402

SUCCESSFUL_REPORT = """Flow report for adder_top
Flow Status\tSuccessful - Mon Aug 11 10:00:00 2026
Quartus Prime Version\t23.1std.0 Build 991
Revision Name\tadder_top
Total logic elements\t9
"""

FAILED_REPORT = """Flow report for adder_top
Flow Status\tFailed - Mon Aug 11 10:00:00 2026
Quartus Prime Version\t23.1std.0 Build 991
"""

SOURCE_GLOBS = ["src", "quartus"]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _hash(repo: Path, path: Path) -> str:
    return _git(repo, "hash-object", "--", str(path))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one design source, committed."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(tmp_path, "config", "user.email", "gate@test")
    _git(tmp_path, "config", "user.name", "Gate Test")

    (tmp_path / "src").mkdir()
    (tmp_path / "quartus").mkdir()
    (tmp_path / "output_files").mkdir()
    (tmp_path / "src" / "adder.sv").write_text("module adder; endmodule\n")
    (tmp_path / "quartus" / "adder_top.qsf").write_text("set_global_assignment -name X Y\n")

    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial design")
    return tmp_path


def _write_artifact(
    repo: Path,
    *,
    report_text: str = SUCCESSFUL_REPORT,
    commit: str | None = None,
    dirty: bool = False,
    sources: dict[str, str] | None = None,
    report_blob: str | None = None,
    schema: int = SCHEMA_VERSION,
) -> Path:
    """Write a report plus its provenance sidecar, with optional corruption."""
    report = repo / "output_files" / "adder.flow.rpt"
    report.write_text(report_text)

    if sources is None:
        sources = {
            "src/adder.sv": _hash(repo, repo / "src" / "adder.sv"),
            "quartus/adder_top.qsf": _hash(repo, repo / "quartus" / "adder_top.qsf"),
        }

    payload = {
        "schema": schema,
        "commit": commit if commit is not None else _git(repo, "rev-parse", "HEAD"),
        "dirty": dirty,
        "generated_at_utc": "2026-08-11T16:00:00Z",
        "sources": sources,
        "report": {
            "path": "output_files/adder.flow.rpt",
            "blob": report_blob if report_blob is not None else _hash(repo, report),
        },
        "tool": {"quartus_version": "23.1std.0", "device": "5CSEBA6U23I7"},
    }
    sidecar = report.with_suffix(report.suffix + SIDECAR_SUFFIX)
    sidecar.write_text(json.dumps(payload, indent=2))
    return report


def _validate(repo: Path, report: Path, **kw):
    return validate(
        repo=repo,
        report_path=report,
        expected_commit=kw.pop("commit", "HEAD"),
        source_globs=kw.pop("source_globs", SOURCE_GLOBS),
        **kw,
    )


# --- Scenario 1: base success -------------------------------------------------


def test_scenario1_valid_artifact_is_accepted(repo: Path) -> None:
    report = _write_artifact(repo)
    result = _validate(repo, report)
    assert result.commit == _git(repo, "rev-parse", "HEAD")
    assert result.source_count == 2
    assert result.tool["device"] == "5CSEBA6U23I7"


# --- Scenario 3: missing artifact ---------------------------------------------


def test_scenario3_missing_report_is_rejected(repo: Path) -> None:
    with pytest.raises(MissingArtifact):
        _validate(repo, repo / "output_files" / "adder.flow.rpt")


def test_scenario3_missing_sidecar_is_rejected(repo: Path) -> None:
    report = _write_artifact(repo)
    report.with_suffix(report.suffix + SIDECAR_SUFFIX).unlink()
    with pytest.raises(MissingArtifact):
        _validate(repo, report)


# --- Scenario 4: failed synthesis ---------------------------------------------


def test_scenario4_failed_compile_is_rejected(repo: Path) -> None:
    report = _write_artifact(repo, report_text=FAILED_REPORT)
    with pytest.raises(SynthesisFailed):
        _validate(repo, report)


def test_scenario4_report_without_status_is_rejected(repo: Path) -> None:
    report = _write_artifact(repo, report_text="Flow report\nTotal logic elements\t9\n")
    with pytest.raises(SynthesisFailed):
        _validate(repo, report)


# --- Scenario 5: stale artifact -----------------------------------------------


def test_scenario5_artifact_from_earlier_commit_is_rejected(repo: Path) -> None:
    """The exact failure the supervisor's `grep Successful` gate lets through."""
    report = _write_artifact(repo)
    # Design moves on; the report stays behind.
    (repo / "src" / "adder.sv").write_text("module adder; wire changed; endmodule\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change the design")

    with pytest.raises(StaleArtifact) as exc:
        _validate(repo, report)
    # Caught by the per-source blob comparison, which is the real staleness
    # test: the sidecar's own commit is a legitimate ancestor here, so commit
    # identity alone could not have caught it.
    assert "changed after synthesis" in str(exc.value)


def test_scenario5_source_changed_after_synthesis_is_rejected(repo: Path) -> None:
    """Sidecar names the right commit but records a different source blob."""
    report = _write_artifact(
        repo,
        sources={
            "src/adder.sv": "0" * 40,
            "quartus/adder_top.qsf": _hash(repo, repo / "quartus" / "adder_top.qsf"),
        },
    )
    with pytest.raises(StaleArtifact) as exc:
        _validate(repo, report)
    assert "changed after synthesis" in str(exc.value)


def test_scenario5_omitted_source_is_rejected(repo: Path) -> None:
    """Dropping a file from the sidecar must not hide it from the gate."""
    report = _write_artifact(
        repo,
        sources={"src/adder.sv": _hash(repo, repo / "src" / "adder.sv")},
    )
    with pytest.raises(StaleArtifact) as exc:
        _validate(repo, report)
    assert "not part of the compile" in str(exc.value)


def test_scenario5_source_absent_at_commit_is_rejected(repo: Path) -> None:
    report = _write_artifact(
        repo,
        sources={
            "src/adder.sv": _hash(repo, repo / "src" / "adder.sv"),
            "quartus/adder_top.qsf": _hash(repo, repo / "quartus" / "adder_top.qsf"),
            "src/ghost.sv": "1" * 40,
        },
    )
    with pytest.raises(StaleArtifact) as exc:
        _validate(repo, report)
    assert "does not exist at" in str(exc.value)


# --- Forgery and hygiene ------------------------------------------------------


def test_hand_edited_report_is_rejected(repo: Path) -> None:
    """`echo Successful > report` must not pass, unlike the baseline gate."""
    report = _write_artifact(repo)
    report.write_text(SUCCESSFUL_REPORT + "tampered\n")
    with pytest.raises(TamperedReport):
        _validate(repo, report)


def test_dirty_tree_is_rejected(repo: Path) -> None:
    report = _write_artifact(repo, dirty=True)
    with pytest.raises(DirtyTree):
        _validate(repo, report)


def test_dirty_tree_allowed_locally_when_opted_in(repo: Path) -> None:
    report = _write_artifact(repo, dirty=True)
    assert _validate(repo, report, allow_dirty=True).source_count == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 99},
        {"commit": "not-a-sha"},
        {"dirty": "false"},
        {"sources": {}},
    ],
)
def test_malformed_sidecar_is_rejected(repo: Path, payload: dict) -> None:
    report = _write_artifact(repo)
    sidecar = report.with_suffix(report.suffix + SIDECAR_SUFFIX)
    raw = json.loads(sidecar.read_text())
    raw.update(payload)
    sidecar.write_text(json.dumps(raw))
    with pytest.raises(MalformedProvenance):
        _validate(repo, report)


def test_unparseable_sidecar_is_rejected(repo: Path) -> None:
    report = _write_artifact(repo)
    report.with_suffix(report.suffix + SIDECAR_SUFFIX).write_text("{not json")
    with pytest.raises(MalformedProvenance):
        _validate(repo, report)


def test_gate_is_deterministic_across_repeated_runs(repo: Path) -> None:
    """Flaky rate for the gate must be zero (OE2)."""
    report = _write_artifact(repo)
    commits = {_validate(repo, report).commit for _ in range(10)}
    assert len(commits) == 1


# --- Regressions found by adversarial review ----------------------------------
# Each of these encodes a defect that was present in the first implementation.
# They are the reason the gate is trustworthy, so none may be deleted without
# a replacement.


def test_honest_workflow_passes_after_the_artifact_is_committed(repo: Path) -> None:
    """The artifact is generated before it can be committed. That must be legal.

    Regression: the gate originally demanded ``prov.commit == HEAD``. Committing
    the report and its sidecar necessarily creates a *new* commit, so that
    condition was unsatisfiable and every honest artifact was rejected as stale.
    """
    report = _write_artifact(repo)
    synthesis_commit = _git(repo, "rev-parse", "HEAD")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit the synthesis artifact")
    evaluated = _git(repo, "rev-parse", "HEAD")

    assert synthesis_commit != evaluated, "the premise of this test"
    result = _validate(repo, report)
    assert result.commit == evaluated


def test_artifact_from_an_unrelated_history_is_rejected(repo: Path) -> None:
    """Ancestry is still required: a report from another branch is not evidence."""
    report = _write_artifact(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "artifact")

    # An orphan branch shares no history with the evaluated commit.
    _git(repo, "checkout", "-q", "--orphan", "elsewhere")
    _git(repo, "commit", "-q", "-m", "unrelated root", "--allow-empty")
    orphan = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")

    payload = json.loads((repo / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}").read_text())
    payload["commit"] = orphan
    (repo / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}").write_text(json.dumps(payload))

    with pytest.raises(StaleArtifact) as exc:
        _validate(repo, report)
    assert "not an ancestor" in str(exc.value)


def test_unsuccessful_is_not_read_as_successful(repo: Path) -> None:
    """'Unsuccessful' contains 'Successful'; a substring search would pass it."""
    report = _write_artifact(
        repo,
        report_text="Flow report\nFlow Status\tUnsuccessful - Mon Aug 11\n",
    )
    with pytest.raises(SynthesisFailed):
        _validate(repo, report)


def test_semicolon_table_is_parsed(repo: Path) -> None:
    """Quartus writes the Flow Summary as a ';'-delimited table, not tabs."""
    report = _write_artifact(
        repo,
        report_text=(
            "+-------------------------------------+\n"
            "; Flow Summary                        ;\n"
            "; Flow Status         ; Successful - Mon Aug 11 10:00:00 2026 ;\n"
            "; Quartus Prime Version ; 23.1std.0 Build 991 ;\n"
        ),
    )
    assert _validate(repo, report).commit


def test_semicolon_table_failure_is_parsed(repo: Path) -> None:
    report = _write_artifact(
        repo,
        report_text="; Flow Status         ; Unsuccessful - Mon Aug 11 ;\n",
    )
    with pytest.raises(SynthesisFailed):
        _validate(repo, report)


def test_pathspec_matching_nothing_is_an_internal_error(repo: Path) -> None:
    """A typo'd --source-glob must not silently disable the coverage check.

    ``git ls-tree -- nosuchdir`` exits 0 with empty output, so the check would
    have passed vacuously and let the sidecar omit any file it liked.
    """
    report = _write_artifact(repo)
    with pytest.raises(GateInternalError) as exc:
        validate(
            repo=repo,
            report_path=report,
            expected_commit="HEAD",
            source_globs=["src", "quartus", "nosuchdir"],
        )
    assert "vacuous" in str(exc.value)


def test_gate_works_when_the_template_is_a_subdirectory(tmp_path: Path) -> None:
    """The real CI layout: the template lives below the git root.

    The workflow runs with ``working-directory: template`` and pathspecs that are
    relative to it. Regression: ``ls-tree`` prints cwd-relative paths while
    ``rev-parse <rev>:<path>`` always resolves from the root, so the two
    disagreed and every source was reported as absent from the commit.
    """
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True,
                   capture_output=True)
    _git(tmp_path, "config", "user.email", "gate@test")
    _git(tmp_path, "config", "user.name", "Gate Test")

    tpl = tmp_path / "template"
    (tpl / "src").mkdir(parents=True)
    (tpl / "quartus").mkdir()
    (tpl / "output_files").mkdir()
    (tpl / "src" / "adder.sv").write_text("module adder; endmodule\n")
    (tpl / "quartus" / "adder_top.qsf").write_text("set_global_assignment -name X Y\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "design")

    report = tpl / "output_files" / "adder.flow.rpt"
    report.write_text(SUCCESSFUL_REPORT)
    payload = {
        "schema": SCHEMA_VERSION,
        "commit": _git(tmp_path, "rev-parse", "HEAD"),
        "dirty": False,
        "generated_at_utc": "2026-08-11T16:00:00Z",
        # Paths in the sidecar are root-relative, as post_flow.tcl writes them.
        "sources": {
            "template/src/adder.sv": _hash(tmp_path, tpl / "src" / "adder.sv"),
            "template/quartus/adder_top.qsf": _hash(
                tmp_path, tpl / "quartus" / "adder_top.qsf"
            ),
        },
        "report": {
            "path": "template/output_files/adder.flow.rpt",
            "blob": _hash(tmp_path, report),
        },
        "tool": {"quartus_version": "23.1std.0", "device": "5CSEBA6U23I7"},
    }
    (tpl / "output_files" / f"adder.flow.rpt{SIDECAR_SUFFIX}").write_text(
        json.dumps(payload)
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "artifact")

    result = validate(
        repo=tpl,  # exactly what `working-directory: template` gives the gate
        report_path=report,
        expected_commit="HEAD",
        source_globs=["src", "quartus"],
    )
    assert result.source_count == 2


def test_report_with_no_flow_status_row_is_rejected(repo: Path) -> None:
    """No recognizable verdict means no approval: never pass by absence."""
    report = _write_artifact(repo, report_text="some unrelated text\n")
    with pytest.raises(SynthesisFailed):
        _validate(repo, report)
