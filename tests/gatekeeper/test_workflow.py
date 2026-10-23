# SPDX-License-Identifier: Apache-2.0
"""Execute the real aggregate verdict without GitHub credentials or runners."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest


@pytest.fixture(scope="module")
def workflow():
    template = Path(__file__).resolve().parents[2]
    candidates = (
        template / ".github/workflows/gatekeeper.yml",
        template.parent / ".github/workflows/gatekeeper.yml",
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.skip("Workflow is not included in this standalone verification payload")


def job(workflow, name):
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
        workflow,
    )
    assert match, f"Missing workflow job: {name}"
    return match.group(1)


@pytest.fixture(scope="module")
def verdict_script(workflow):
    text = job(workflow, "gatekeeper")
    match = re.search(r"(?m)^        run: \|\n((?:          .*\n|\n)+)", text)
    assert match, "The aggregate verdict must have an executable script"
    script = textwrap.dedent(match.group(1))
    assert "${{" not in script, "Pass job results via env, not script interpolation"
    return script


def decide(script, smoke, verification):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env={**os.environ, "SMOKE_RESULT": smoke, "VERIFICATION_RESULT": verification},
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("smoke", ["success", "failure", "cancelled", "skipped", "", "unknown"])
@pytest.mark.parametrize("verification", ["success", "failure", "cancelled", "skipped", "", "unknown"])
def test_only_explicit_success_of_every_required_job_passes(verdict_script, smoke, verification):
    result = decide(verdict_script, smoke, verification)
    expected = smoke == verification == "success"
    assert (result.returncode == 0) == expected, result.stdout + result.stderr
    assert ("::error title=Gatekeeper::" in result.stdout) == (not expected)


def test_four_concurrent_local_actors_do_not_share_verdicts(verdict_script):
    scenarios = [("success", "success"), ("failure", "success"),
                 ("success", "cancelled"), ("skipped", "success")] * 8
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda pair: decide(verdict_script, *pair), scenarios))
    assert [result.returncode for result in results] == [0, 1, 1, 1] * 8


def test_verdict_always_runs_and_has_no_checkout(workflow):
    text = job(workflow, "gatekeeper")
    assert "needs: [smoke, verification]" in text
    assert "if: always()" in text
    assert "timeout-minutes: 5" in text
    assert "working-directory: ." in text
    assert "SMOKE_RESULT: ${{ needs.smoke.result }}" in text
    assert "VERIFICATION_RESULT: ${{ needs.verification.result }}" in text
    assert "actions/checkout@" not in text
    assert "continue-on-error" not in text


def test_prs_and_merge_queue_have_unfiltered_required_checks(workflow):
    events = workflow.split("\npermissions:", 1)[0].split("\non:\n", 1)[1]
    assert "pull_request:\n    branches: [master, main, dev]" in events
    assert "merge_group:\n    types: [checks_requested]" in events
    assert "paths:" not in events and "paths-ignore:" not in events
    assert "pull_request_target:" not in events
    assert "group: gatekeeper-${{ github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow
    for name in ("smoke", "verification"):
        text = job(workflow, name)
        assert not re.search(r"(?m)^    (if|continue-on-error):", text)


def test_library_evidence_is_required_not_merely_nonempty(workflow):
    text = job(workflow, "verification")
    assert "bash -e -c 'make library-regression" in text
    assert "--junit results/junit-library.xml" in text
    assert "--output results/report-library.md --require-pass" in text
    assert "test -s results/junit-library.xml" in text
    assert "test -s results/report-library.md" in text
    assert "if-no-files-found: error" in text


def test_publishing_cannot_follow_a_pr_or_a_failed_required_job(workflow):
    text = job(workflow, "publish-image")
    assert "needs: [smoke, verification]" in text
    assert ("if: github.event_name == 'push' && needs.smoke.result == 'success' "
            "&& needs.verification.result == 'success'") in text
