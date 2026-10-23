# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Validate a locally produced Quartus synthesis artifact against a commit.

This is check 3 of the gatekeeper pipeline. Synthesis runs natively on a lab
station because the Quartus license is node-locked, so CI is handed a report it
cannot regenerate. This module decides whether that report is admissible
evidence for the commit under evaluation.

Threat model
------------
The gate defends against *accidental and low-effort* substitution: a report left
over from an earlier design revision, a report produced from a dirty tree, a
report hand-edited to say "Successful". It is not a cryptographic proof of
origin, because the sidecar is produced on a machine the student controls. See
``docs/decisions/ADR-0006-compuerta-artefacto.md`` for the argument, for where
the requirement comes from (the anteproyecto's check 3 and OE4 dry-run scenario
5, not the course repos), and for the upgrade path -- running synthesis on a
lab-hosted runner -- that removes the assumption entirely.

Failure modes map onto the dry-run scenarios of OE4:
  * scenario 3 -- missing artifact  -> ``MissingArtifact``
  * scenario 4 -- failed synthesis  -> ``SynthesisFailed``
  * scenario 5 -- stale artifact    -> ``StaleArtifact``
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from provenance import (
    Provenance,
    ProvenanceError,
    ProvenanceMissing,
    SIDECAR_SUFFIX,
)

#: Label of the Flow Summary row that carries the compile verdict.
STATUS_LABEL = "Flow Status"

#: Value prefix Quartus writes for a successful compile. Matched case-sensitively
#: and as a *prefix of the value cell*, never as a substring of the whole line:
#: Quartus writes "Unsuccessful" for a failure, which contains "successful".
SUCCESS_VALUE_PREFIX = "Successful"


class GateError(Exception):
    """Base class for a rejected artifact."""

    #: Short stable code, used in CI output and in the dry-run matrix.
    code = "GATE"


class GateInternalError(GateError):
    """The gate could not reach a verdict (git failure, unfetched commit, ...).

    Deliberately *not* one of the seven artifact-rejection modes. Conflating
    "the evidence is bad" with "we could not read the evidence" makes the
    dry-run matrix unmappable and sends a student chasing the wrong fault.
    """

    code = "GATE_INTERNAL"


class MissingArtifact(GateError):
    """The report or its provenance sidecar is absent (dry-run scenario 3)."""

    code = "MISSING_ARTIFACT"


class MalformedProvenance(GateError):
    """The sidecar exists but is not a valid provenance record."""

    code = "MALFORMED_PROVENANCE"


class DirtyTree(GateError):
    """The artifact was produced from a tree with uncommitted changes."""

    code = "DIRTY_TREE"


class StaleArtifact(GateError):
    """The artifact does not correspond to the evaluated commit (scenario 5)."""

    code = "STALE_ARTIFACT"


class TamperedReport(GateError):
    """The report content no longer matches what the sidecar recorded."""

    code = "TAMPERED_REPORT"


class SynthesisFailed(GateError):
    """The report is authentic but records an unsuccessful compile (scenario 4)."""

    code = "SYNTHESIS_FAILED"


@dataclass(frozen=True)
class GateResult:
    """Outcome of a successful validation, for logging."""

    commit: str
    report_path: str
    source_count: int
    tool: dict[str, str]


class Git:
    """Thin wrapper over the git plumbing commands the gate needs.

    Every path this class accepts or returns is **relative to the repository
    root**, never to the invocation directory. ``git ls-tree`` prints paths
    relative to the cwd prefix while ``git rev-parse <rev>:<path>`` always
    resolves from the root, so mixing the two silently breaks whenever the gate
    runs from a subdirectory -- which is exactly how CI invokes it. Anchoring to
    ``--show-toplevel`` removes the discrepancy at the source.
    """

    def __init__(self, repo: Path) -> None:
        self._cwd = repo
        self.root = Path(self._raw("rev-parse", "--show-toplevel").strip())
        #: Path of the invocation directory relative to the root, POSIX style,
        #: with a trailing slash (empty when invoked at the root). Pathspecs the
        #: caller supplies are relative to *this*, while everything git is asked
        #: is expressed relative to the root, so this is what bridges them.
        prefix = self._raw("rev-parse", "--show-prefix").strip()
        self.prefix = prefix

    def _raw(self, *args: str) -> str:
        """Run git in the invocation directory. Used only to find the root."""
        try:
            proc = subprocess.run(
                ["git", "-C", str(self._cwd), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:  # git absent, cwd gone
            raise GateInternalError(f"cannot execute git: {exc}") from exc
        if proc.returncode != 0:
            raise GateInternalError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout

    def _run(self, *args: str) -> str:
        """Run git anchored at the repository root."""
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise GateInternalError(f"cannot execute git: {exc}") from exc
        if proc.returncode != 0:
            raise GateInternalError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout

    def relpath(self, path: Path) -> str:
        """Express an on-disk path relative to the repository root, POSIX style."""
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError as exc:
            raise GateInternalError(
                f"{path} is outside the repository at {self.root}"
            ) from exc

    def resolve(self, rev: str) -> str:
        """Return the full SHA for a revision."""
        return self._run("rev-parse", "--verify", f"{rev}^{{commit}}").strip()

    def hash_file(self, path: Path) -> str:
        """Return the git blob id of a file on disk."""
        return self._run("hash-object", "--", str(path.resolve())).strip()

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        """Return True if the first commit is an ancestor of (or equal to) the second."""
        proc = subprocess.run(
            ["git", "-C", str(self.root), "merge-base", "--is-ancestor",
             maybe_ancestor, descendant],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        # Any other code means git could not answer (unknown commit, shallow
        # clone). That is an internal failure, not a "no".
        raise GateInternalError(
            f"cannot determine ancestry of {maybe_ancestor[:12]}: {proc.stderr.strip()}"
        )

    def blob_at(self, commit: str, path: str) -> str | None:
        """Return the blob id of ``path`` at ``commit``, or None if truly absent.

        Only a genuine "path is not in this tree" answer yields ``None``. Any
        other git failure propagates, so an infrastructure fault is never
        reported to the student as a stale source.
        """
        proc = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--verify",
             f"{commit}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
        stderr = proc.stderr.lower()
        if "exists on disk, but not in" in stderr or "does not exist in" in stderr:
            return None
        if "needed a single revision" in stderr or "unknown revision" in stderr:
            # git's generic answer when the path is absent from the tree.
            if self._path_absent(commit, path):
                return None
        raise GateInternalError(
            f"cannot resolve {path!r} at {commit[:12]}: {proc.stderr.strip()}"
        )

    def _path_absent(self, commit: str, path: str) -> bool:
        """Confirm via ls-tree that ``path`` really is not tracked at ``commit``."""
        out = self._run("ls-tree", "-r", "--full-name", "--name-only", commit,
                        "--", path)
        return not out.strip()

    def files_at(self, commit: str, pathspec: str) -> list[str]:
        """List tracked files under ``pathspec`` at ``commit``, root-relative."""
        out = self._run("ls-tree", "-r", "--full-name", "--name-only", commit,
                        "--", pathspec)
        return [line for line in out.splitlines() if line]

    def qualify(self, pathspec: str) -> str:
        """Rebase a caller-supplied pathspec onto the repository root."""
        return f"{self.prefix}{pathspec}" if self.prefix else pathspec


def _flow_status(text: str) -> str | None:
    """Extract the Flow Summary status value, or None if there is no such row.

    Quartus renders the Flow Summary as a semicolon-delimited table::

        ; Flow Status                    ; Successful - Mon Aug 11 ... ;

    so the verdict lives in a *cell*, and it must be read as one. Searching the
    whole line for "Successful" is unsafe: the failure value is "Unsuccessful",
    which contains it. Tab-separated exports are also accepted.
    """
    for line in text.splitlines():
        if STATUS_LABEL not in line:
            continue
        cells = [c.strip() for c in line.split(";")] if ";" in line else \
                [c.strip() for c in line.split("\t")]
        for index, cell in enumerate(cells):
            if cell == STATUS_LABEL and index + 1 < len(cells):
                return cells[index + 1]
    return None


def _report_says_success(text: str) -> bool:
    """Decide whether a Quartus flow report records a successful compile.

    Returns False when no Flow Status row can be found at all. A report the gate
    cannot read is not a report the gate may approve -- the pipeline must never
    pass by absence of evidence.
    """
    status = _flow_status(text)
    if status is None:
        return False
    return status.startswith(SUCCESS_VALUE_PREFIX)


def validate(
    repo: Path,
    report_path: Path,
    expected_commit: str,
    source_globs: list[str],
    allow_dirty: bool = False,
) -> GateResult:
    """Validate a synthesis artifact against the commit under evaluation.

    Args:
        repo: Repository root.
        report_path: Path to the Quartus flow report.
        expected_commit: Revision CI is evaluating (any git revision syntax).
        source_globs: Pathspecs enumerating the design sources that must have
            been compiled. Every tracked file matching these at the evaluated
            commit must appear in the sidecar, which is what prevents a changed
            file from being silently omitted.
        allow_dirty: Escape hatch for local pre-commit use. Never set in CI.

    Returns:
        A GateResult describing the accepted artifact.

    Raises:
        GateError: Any admissibility failure; the subclass identifies which.
    """
    git = Git(repo)
    head = git.resolve(expected_commit)

    if not report_path.is_file():
        raise MissingArtifact(
            f"synthesis report not found at {report_path}. "
            "Run the Quartus compile on a lab station and commit the artifact."
        )

    sidecar_path = report_path.with_suffix(report_path.suffix + SIDECAR_SUFFIX)
    try:
        prov = Provenance.load(sidecar_path)
    except ProvenanceMissing as exc:
        raise MissingArtifact(str(exc)) from exc
    except ProvenanceError as exc:
        raise MalformedProvenance(str(exc)) from exc

    if prov.dirty and not allow_dirty:
        raise DirtyTree(
            "the artifact was generated from a working tree with uncommitted "
            "changes; commit the design before compiling so the report can be "
            "attributed to a revision."
        )

    # The artifact is generated *before* it can be committed, so demanding
    # prov.commit == head is unsatisfiable: adding the report and its sidecar to
    # the tree necessarily produces a new SHA, and on pull_request events
    # GITHUB_SHA is a synthetic merge commit the lab station never saw. What is
    # actually required is that synthesis ran on an ancestor of what is being
    # evaluated -- the per-source blob comparison below is what proves the design
    # itself did not move in between, and it is the real staleness test.
    if prov.commit != head and not git.is_ancestor(prov.commit, head):
        raise StaleArtifact(
            f"artifact was generated at commit {prov.commit[:12]}, which is not "
            f"an ancestor of the evaluated commit {head[:12]}. The report comes "
            "from an unrelated history; recompile on this branch."
        )

    actual_report_blob = git.hash_file(report_path)
    if actual_report_blob != prov.report.blob:
        raise TamperedReport(
            f"report content does not match the provenance record "
            f"(recorded {prov.report.blob[:12]}, found {actual_report_blob[:12]}). "
            "The report was modified after it was generated."
        )

    # Every source compiled must match the evaluated commit byte for byte.
    for path, recorded_blob in sorted(prov.sources.items()):
        blob_at_commit = git.blob_at(head, path)
        if blob_at_commit is None:
            raise StaleArtifact(
                f"source {path!r} was compiled but does not exist at {head[:12]}."
            )
        if blob_at_commit != recorded_blob:
            raise StaleArtifact(
                f"source {path!r} changed after synthesis "
                f"(compiled {recorded_blob[:12]}, evaluated {blob_at_commit[:12]}). "
                "Recompile so the report reflects the design under review."
            )

    # And every source at the evaluated commit must have been compiled, so that
    # dropping a file from the sidecar cannot hide a change.
    expected_sources: set[str] = set()
    for spec in source_globs:
        qualified = git.qualify(spec)
        matched = git.files_at(head, qualified)
        if not matched:
            # `git ls-tree -- nosuchdir` exits 0 with empty output, so a typo'd
            # or stale pathspec would silently reduce this check to a no-op and
            # let the sidecar omit whatever it liked. Refuse instead.
            raise GateInternalError(
                f"source pathspec {qualified!r} matches no tracked file at "
                f"{head[:12]}; the source-coverage check would be vacuous. "
                "Fix --source-glob so it describes the design."
            )
        expected_sources.update(matched)
    if not expected_sources:
        raise GateInternalError(
            "no design sources found at the evaluated commit; refusing to "
            "approve an artifact that covers nothing."
        )
    missing = expected_sources - set(prov.sources)
    if missing:
        raise StaleArtifact(
            "these design sources exist at the evaluated commit but were not "
            f"part of the compile: {', '.join(sorted(missing))}."
        )

    report_text = report_path.read_text(encoding="utf-8", errors="replace")
    if not _report_says_success(report_text):
        raise SynthesisFailed(
            f"{report_path} records an unsuccessful Quartus compile."
        )

    return GateResult(
        commit=head,
        report_path=str(report_path),
        source_count=len(prov.sources),
        tool=prov.tool,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="artifact_gate",
        description="Validate a locally produced Quartus report against a commit.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    parser.add_argument("--report", type=Path, required=True, help="path to the flow report")
    parser.add_argument("--commit", default="HEAD", help="commit under evaluation")
    parser.add_argument(
        "--source-glob",
        action="append",
        default=None,
        dest="source_globs",
        help="pathspec of design sources that must have been compiled (repeatable)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="permit artifacts from a dirty tree (local use only, never in CI)",
    )
    args = parser.parse_args(argv)

    try:
        result = validate(
            repo=args.repo,
            report_path=args.report,
            expected_commit=args.commit,
            source_globs=args.source_globs or ["src", "lib", "quartus"],
            allow_dirty=args.allow_dirty,
        )
    except GateInternalError as exc:
        # Exit 2, not 1: the gate reached no verdict on the artifact. Reporting
        # an infrastructure fault as an artifact rejection sends the student to
        # debug their design instead of the pipeline.
        print(f"::error title=Artifact gate [{exc.code}]::{exc}", file=sys.stderr)
        return 2
    except GateError as exc:
        print(f"::error title=Artifact gate [{exc.code}]::{exc}", file=sys.stderr)
        return 1

    print(
        f"Artifact gate passed: {result.report_path} is bound to commit "
        f"{result.commit[:12]} across {result.source_count} source file(s)."
    )
    if result.tool:
        details = ", ".join(f"{k}={v}" for k, v in sorted(result.tool.items()))
        print(f"  tool: {details}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
