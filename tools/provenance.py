# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Provenance record for a locally produced Quartus synthesis artifact.

The synthesis flow runs natively on a lab station because the Quartus/Questa
licenses are node-locked, so CI cannot regenerate the report itself. It can only
be handed a report the student produced. This module defines the evidence that
must travel with that report so CI can decide whether to trust it.

The design rule is that no hashing is implemented here. Every content hash is a
``git hash-object`` blob id produced by git itself, both when the sidecar is
written on the lab station and when it is verified in CI. That keeps the two
sides byte-identical by construction and removes any TCL-side crypto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: Filename of the sidecar, written next to the report it describes.
SIDECAR_SUFFIX = ".provenance.json"


class ProvenanceError(ValueError):
    """The sidecar is absent, unparseable, or structurally invalid."""


class ProvenanceMissing(ProvenanceError):
    """The sidecar file does not exist.

    A distinct type rather than a distinguishable message: the gate maps this to
    ``MissingArtifact`` (dry-run scenario 3) and everything else to
    ``MalformedProvenance``, and coupling two modules through prose is how that
    mapping silently rots.
    """


@dataclass(frozen=True)
class ReportRef:
    """The synthesis report the sidecar vouches for."""

    path: str
    blob: str


@dataclass(frozen=True)
class Provenance:
    """What the lab station asserts about how the report was produced.

    Attributes:
        commit: Full 40-hex commit SHA that was checked out at generation time.
        dirty: Whether the working tree had uncommitted changes to tracked files.
        sources: Design source path -> git blob id, as compiled.
        report: The report this record vouches for.
        generated_at_utc: ISO-8601 timestamp, informational only.
        tool: Free-form tool metadata (Quartus version, device, family).
    """

    commit: str
    dirty: bool
    sources: dict[str, str]
    report: ReportRef
    generated_at_utc: str = ""
    tool: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any) -> "Provenance":
        """Build a Provenance from parsed JSON, validating structure.

        Raises:
            ProvenanceError: If the payload is not a well-formed sidecar.
        """
        if not isinstance(raw, dict):
            raise ProvenanceError("sidecar root must be a JSON object")

        schema = raw.get("schema")
        if schema != SCHEMA_VERSION:
            raise ProvenanceError(
                f"unsupported sidecar schema {schema!r}, expected {SCHEMA_VERSION}"
            )

        commit = raw.get("commit")
        if not isinstance(commit, str) or not _is_sha1(commit):
            raise ProvenanceError(f"'commit' must be a 40-hex sha, got {commit!r}")

        dirty = raw.get("dirty")
        if not isinstance(dirty, bool):
            raise ProvenanceError(f"'dirty' must be a boolean, got {dirty!r}")

        sources = raw.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise ProvenanceError("'sources' must be a non-empty object")
        for path, blob in sources.items():
            if not isinstance(path, str) or not isinstance(blob, str) or not _is_sha1(blob):
                raise ProvenanceError(f"invalid source entry {path!r}: {blob!r}")

        report_raw = raw.get("report")
        if not isinstance(report_raw, dict):
            raise ProvenanceError("'report' must be an object")
        report_path = report_raw.get("path")
        report_blob = report_raw.get("blob")
        if not isinstance(report_path, str) or not report_path:
            raise ProvenanceError("'report.path' must be a non-empty string")
        if not isinstance(report_blob, str) or not _is_sha1(report_blob):
            raise ProvenanceError(f"'report.blob' must be a 40-hex sha, got {report_blob!r}")

        tool = raw.get("tool", {})
        if not isinstance(tool, dict):
            raise ProvenanceError("'tool' must be an object")

        generated = raw.get("generated_at_utc", "")
        if not isinstance(generated, str):
            raise ProvenanceError("'generated_at_utc' must be a string")

        return cls(
            commit=commit,
            dirty=dirty,
            sources=dict(sources),
            report=ReportRef(path=report_path, blob=report_blob),
            generated_at_utc=generated,
            tool={str(k): str(v) for k, v in tool.items()},
        )

    @classmethod
    def load(cls, path: Path) -> "Provenance":
        """Read and validate a sidecar from disk.

        Raises:
            ProvenanceError: If the file is missing or invalid.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ProvenanceMissing(f"provenance sidecar not found: {path}") from exc
        except OSError as exc:
            raise ProvenanceError(f"cannot read provenance sidecar {path}: {exc}") from exc

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProvenanceError(f"provenance sidecar {path} is not valid JSON: {exc}") from exc

        return cls.from_dict(raw)


def _is_sha1(value: str) -> bool:
    """Return True if value is a 40-character lowercase-or-uppercase hex string."""
    if len(value) != 40:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
