# SPDX-License-Identifier: Apache-2.0
"""Deployment contracts only: no GitHub, SSH, SLURM, Quartus or board operations."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

TEMPLATE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TEMPLATE / "tools"))
import deployment_guard as guard
import fpga_deploy as fpga
import kabre_deploy as kabre

SHA = "a" * 40
DIGEST = "b" * 64
REPO = "Katharsis0/TFG"


@pytest.mark.parametrize("path", ["", "branches/master", "actions/runs/123/artifacts?per_page=100&page=1"])
def test_api_uses_canonical_repository_url(monkeypatch, path):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    requests = []

    def respond(request, timeout):
        requests.append((request.full_url, timeout))
        return io.StringIO('{"default_branch": "master"}')

    monkeypatch.setattr(guard, "urlopen", respond)
    assert guard.api(path) == {"default_branch": "master"}
    expected = f"https://api.github.com/repos/{REPO}"
    if path:
        expected += f"/{path}"
    assert requests == [(expected, 30)]


def good_run():
    return {"event": "push", "path": ".github/workflows/gatekeeper.yml",
            "head_sha": SHA, "head_repository": {"full_name": REPO},
            "status": "completed", "conclusion": "success"}


def test_reference_run_requires_trusted_successful_push():
    guard.validate_run(good_run(), SHA, REPO)


@pytest.mark.parametrize("key,value", [
    ("event", "pull_request"), ("event", "workflow_dispatch"),
    ("path", ".github/workflows/other.yml"), ("head_sha", "f" * 40),
    ("head_repository", {"full_name": "attacker/TFG"}),
    ("status", "in_progress"), ("conclusion", "failure"), ("conclusion", "skipped"),
])
def test_reference_run_rejects_untrusted_or_missing_success(key, value):
    run = good_run()
    run[key] = value
    with pytest.raises(ValueError):
        guard.validate_run(run, SHA, REPO)


def test_explicit_allowlist_allows_operator_own_dispatch_but_not_other_rerunner():
    guard.validate_operators("Katharsis0", "Katharsis0", "Katharsis0")
    for allowlist, actor, rerunner in (("", "Katharsis0", "Katharsis0"),
                                      ("Katharsis0", "other", "Katharsis0"),
                                      ("Katharsis0", "Katharsis0", "other")):
        with pytest.raises(ValueError):
            guard.validate_operators(allowlist, actor, rerunner)


def bundle(tmp_path):
    output = tmp_path / "output_files"
    output.mkdir()
    sof = output / "station.sof"
    sof.write_bytes(b"exact build")
    sof_hash = fpga.digest(sof)
    data = {"inputs": {"commit": SHA, "dirty": False}, "gate_accepted": True,
            "artifacts": {"output_files/station.sof": sof_hash}}
    manifest = tmp_path / "station-evidence.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return sof_hash, fpga.digest(manifest), manifest


def test_bundle_is_bound_to_exact_sof_manifest_and_commit(tmp_path):
    sof, manifest_hash, _ = bundle(tmp_path)
    fpga.validate_bundle(tmp_path, sof, manifest_hash, SHA)
    with pytest.raises(ValueError, match="commit"):
        fpga.validate_bundle(tmp_path, sof, manifest_hash, "f" * 40)
    (tmp_path / "output_files/station.sof").write_bytes(b"other build")
    with pytest.raises(ValueError, match="SOF"):
        fpga.validate_bundle(tmp_path, sof, manifest_hash, SHA)


@pytest.mark.parametrize("change", ["manifest", "dirty", "gate", "hash"])
def test_bundle_rejects_tamper_and_unclean_compile(tmp_path, change):
    sof, manifest_hash, manifest = bundle(tmp_path)
    data = json.loads(manifest.read_text())
    if change == "manifest":
        manifest.write_text("{}")
    elif change == "hash":
        sof = "not-a-hash"
    else:
        if change == "dirty":
            data["inputs"]["dirty"] = True
        else:
            data["gate_accepted"] = False
        manifest.write_text(json.dumps(data))
        manifest_hash = fpga.digest(manifest)
    with pytest.raises(ValueError):
        fpga.validate_bundle(tmp_path, sof, manifest_hash, SHA)


def test_programmer_uses_exact_native_sof_not_glob_or_rbf(tmp_path):
    sof = tmp_path / "station.sof"
    argv = fpga.programming_argv(tmp_path, sof, "DE-SoC [USB-1]", "2")
    assert argv == [str(tmp_path / "quartus_pgm.exe"), "-m", "JTAG",
                    "-c", "DE-SoC [USB-1]", "-o", f"p;{sof}@2"]
    with pytest.raises(ValueError):
        fpga.programming_argv(tmp_path, sof, "x", "2;anything")
    with pytest.raises(ValueError):
        fpga.programming_argv(tmp_path, tmp_path / "other@2.sof", "x", "2")
    with pytest.raises(ValueError):
        fpga.programming_argv(tmp_path, sof, "", "2")


def pilot_config():
    return {"partition": "kura-wide", "image_sha256": DIGEST,
            "cpus": 1, "memory_mb": 2048, "time_minutes": 5,
            "max_tasks": 1, "max_concurrency": 1, "max_cpus": 1,
            "max_memory_mb": 2048, "max_time_minutes": 5}


def image_receipt():
    return {"source_sha": SHA, "oci_digest": f"sha256:{DIGEST}", "sif_sha256": DIGEST}


def test_current_image_receipt_and_pilot_budget():
    kabre.validate_image(pilot_config(), image_receipt(), SHA, f"sha256:{DIGEST}")


@pytest.mark.parametrize("key,value", [
    ("source_sha", "f" * 40), ("oci_digest", "latest"), ("sif_sha256", "f" * 64),
])
def test_current_image_rejects_old_receipt(key, value):
    receipt = image_receipt()
    receipt[key] = value
    with pytest.raises(ValueError):
        kabre.validate_image(pilot_config(), receipt, SHA, f"sha256:{DIGEST}")


@pytest.mark.parametrize("key,value", [
    ("partition", "nukwa"), ("partition", "other"), ("max_tasks", 2),
    ("max_concurrency", 2), ("cpus", 2), ("max_cpus", True),
    ("time_minutes", 10), ("image_sha256", None),
])
def test_pilot_cannot_silently_expand_or_unpin_image(key, value):
    config = pilot_config()
    config[key] = value
    with pytest.raises(ValueError):
        kabre.validate_image(config, image_receipt(), SHA, f"sha256:{DIGEST}")


def test_cancel_only_exact_receipted_unfinished_job(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    receipt = {"job_id": "123", "manifest_sha256": kabre.sha256(manifest)}
    (tmp_path / "submission.json").write_text(json.dumps(receipt))
    calls = []
    monkeypatch.setattr(kabre, "command", lambda argv, *args: calls.append(argv) or 0)
    kabre.cleanup(tmp_path, tmp_path / "evidence")
    assert calls == [["scancel", "123"]]
    (tmp_path / "summary.json").write_text(json.dumps({"complete": True}))
    kabre.cleanup(tmp_path, tmp_path / "evidence")
    assert len(calls) == 1


def test_uncertain_submission_never_guesses_cancellation(monkeypatch, tmp_path):
    (tmp_path / "submission.json").write_text('{"status": "submission-attempted"}')
    monkeypatch.setattr(kabre, "command", lambda *args: pytest.fail("must not cancel"))
    with pytest.raises(ValueError, match="ambiguous"):
        kabre.cleanup(tmp_path, tmp_path / "evidence")


def test_reference_checks_require_both_simulators_and_station(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(fpga, "invoke", lambda argv, *args: calls.append(argv))
    fpga.reference_checks(tmp_path, tmp_path, station=True)
    assert len(calls) == 2
    assert all("--station-manifest" in argv and "check" in argv for argv in calls)
    assert "verilator.json" in calls[0][calls[0].index("--reference") + 1]
    assert "icarus.json" in calls[1][calls[1].index("--reference") + 1]


def test_native_failures_preserve_stderr_and_fail_closed(monkeypatch, tmp_path):
    class Result:
        returncode = 7
        stdout = ""
        stderr = "native error"
    monkeypatch.setattr(fpga.subprocess, "run", lambda *args, **kwargs: Result())
    path = tmp_path / "failed.json"
    with pytest.raises(ValueError, match="failed"):
        fpga.invoke(["not-executed"], path, 30)
    assert json.loads(path.read_text())["stderr"] == "native error"


def test_timeouts_preserve_partial_native_diagnostics(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["not-executed"], 2, output=b"partial", stderr=b"blocked")
    monkeypatch.setattr(fpga.subprocess, "run", timeout)
    path = tmp_path / "timeout.json"
    with pytest.raises(subprocess.TimeoutExpired):
        fpga.invoke(["not-executed"], path, 2)
    data = json.loads(path.read_text())
    assert data["timed_out"] is True and data["stdout"] == "partial" and data["stderr"] == "blocked"


def test_claim_without_receipt_requires_manual_inspection(tmp_path):
    (tmp_path / "submission-claim.json").write_text("{}")
    with pytest.raises(ValueError, match="claim without receipt"):
        kabre.cleanup(tmp_path, tmp_path / "evidence")


@pytest.mark.parametrize("wait_code", [0, 1])
def test_pilot_invokes_exact_bounded_controller_contract(monkeypatch, tmp_path, wait_code):
    image = tmp_path / "current.sif"
    image.write_bytes(b"current image")
    config = pilot_config()
    config.update(image=str(image), image_sha256=kabre.sha256(image))
    receipt = image_receipt()
    receipt["sif_sha256"] = config["image_sha256"]
    campaign, evidence = tmp_path / "campaign", tmp_path / "evidence"
    campaign.mkdir()
    receipt_path = campaign / "image-receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    manifest = campaign / "manifest.json"
    manifest.write_text(json.dumps({"source": {"git_sha": SHA, "dirty": False}, "config": config}))
    monkeypatch.setattr(kabre.sys, "platform", "linux")
    monkeypatch.setattr(kabre.subprocess, "check_output", lambda *args, **kwargs: SHA)
    calls = []
    def fake_command(argv, *args):
        calls.append(argv)
        if "wait" in argv:
            (campaign / "summary.json").write_text(json.dumps(
                {"complete": wait_code == 0, "verdict": "PASS" if wait_code == 0 else "NOT_PASS"}))
            return wait_code
        return 0
    monkeypatch.setattr(kabre, "command", fake_command)
    if wait_code:
        with pytest.raises(ValueError, match="Pilot failed"):
            kabre.run_remote(campaign, evidence, SHA, f"sha256:{DIGEST}", kabre.sha256(manifest))
    else:
        kabre.run_remote(campaign, evidence, SHA, f"sha256:{DIGEST}", kabre.sha256(manifest))
    assert len(calls) == 2
    assert calls[0][-2:] == ["--require-idle", "--execute"]
    assert calls[1][-4:] == ["--timeout-seconds", "600", "--poll-seconds", "15"]


@pytest.mark.parametrize("probe_ready", [True, False])
def test_native_program_and_replay_contract_without_physical_calls(monkeypatch, tmp_path, probe_ready):
    root = tmp_path / "bundle"
    root.mkdir()
    sof, manifest, _ = bundle(root)
    evidence = tmp_path / "program"
    summary = tmp_path / "summary.md"
    (root / "deployment-binding.json").write_text(json.dumps(
        {"source_sha": SHA, "verification_run_id": "99", "reference_artifact_id": "88",
         "build_run_id": "111", "build_run_attempt": "1", "sof_sha256": sof, "manifest_sha256": manifest}))
    for key, value in {"SOURCE_SHA": SHA, "SOF_SHA256": sof, "MANIFEST_SHA256": manifest,
                       "QUARTUS_BIN": str(tmp_path), "BOARD_REVISION": "RevF0",
                       "JTAG_CABLE": "DE-SoC [USB-1]", "FPGA_INDEX": "2",
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                       "BUILD_RUN_ID": "111", "BUILD_RUN_ATTEMPT": "1", "VERIFICATION_RUN_ID": "99",
                       "REFERENCE_ARTIFACT_ID": "88", "PROGRAMMING_RUN_ID": "123", "PROGRAMMING_RUN_ATTEMPT": "1",
                       "KEY0_CONFIRMED": "true",
                       "GITHUB_STEP_SUMMARY": str(summary)}.items():
        monkeypatch.setenv(key, value)
    # Replace only the adapter's OS view, not global pathlib's OS selection.
    monkeypatch.setattr(fpga, "os", SimpleNamespace(name="nt", environ=fpga.os.environ))
    monkeypatch.setattr(fpga.subprocess, "check_output",
                        lambda argv, **kwargs: SHA if "rev-parse" in argv else "")
    monkeypatch.setattr(fpga, "reference_checks", lambda *args, **kwargs: None)
    monkeypatch.setattr(fpga, "output", lambda *args: None)
    calls = []
    def fake_invoke(argv, *args, **kwargs):
        calls.append(argv)
        if "preflight" in argv:
            assert kwargs["expected_code"] == 1
            return json.dumps({"quartus_18_1_detected": True, "jtag": {"state": "enumerated"},
                               "python_jtag_driver": {"native_symbols_resolved": probe_ready,
                                                      "adapter_api_resolved": probe_ready}})
        if "run" in argv:
            Path(argv[argv.index("--output") + 1]).write_text(
                json.dumps({"status": "transaction_agreement_passed"}))
        return ""
    monkeypatch.setattr(fpga, "invoke", fake_invoke)
    monkeypatch.setattr(sys, "argv", ["fpga_deploy.py", "program", "--bundle", str(root),
                                    "--evidence", str(evidence)])
    if not probe_ready:
        with pytest.raises(ValueError, match="ABI/API"):
            fpga.main()
        assert len(calls) == 1
        assert not (evidence / "programming.json").exists()
        return
    fpga.main()
    assert Path(calls[1][0]).name == "quartus_pgm.exe"
    assert calls[1][-1] == f"p;{root / 'output_files/station.sof'}@2"
    proof = evidence / "programming.json"
    monkeypatch.setenv("PROGRAMMING_SHA256", fpga.digest(proof))
    monkeypatch.setattr(sys, "argv", ["fpga_deploy.py", "replay", "--bundle", str(root),
                                    "--evidence", str(tmp_path / "replay"), "--programming-evidence", str(proof)])
    fpga.main()
    assert "--device=-1" in calls[-1] and "--confirm-board-ready" in calls[-1]
    monkeypatch.setenv("PROGRAMMING_RUN_ATTEMPT", "2")
    before = len(calls)
    with pytest.raises(ValueError, match="run_attempt"):
        fpga.main()
    assert len(calls) == before, "An old programming attempt must not reach the physical transport."
    monkeypatch.setenv("PROGRAMMING_RUN_ATTEMPT", "1")
    monkeypatch.setenv("KEY0_CONFIRMED", "false")
    with pytest.raises(ValueError, match="KEY0"):
        fpga.main()
    assert len(calls) == before


def test_guard_does_not_launch_privileged_work_when_allowlist_missing(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["deployment_guard.py", "fpga"])
    for key, value in {"SOURCE_SHA": SHA, "VERIFICATION_RUN_ID": "123",
                       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/master",
                       "GITHUB_SHA": SHA, "GITHUB_REPOSITORY": REPO,
                       "DEPLOYMENT_OPERATORS": "", "GITHUB_ACTOR": "Katharsis0",
                       "GITHUB_TRIGGERING_ACTOR": "Katharsis0"}.items():
        monkeypatch.setenv(key, value)
    def fake_api(path):
        if path == "":
            return {"default_branch": "master"}
        if path.startswith("branches/"):
            return {"protected": True}
        if path.startswith("actions/runs/"):
            return good_run()
        return {"protection_rules": []}
    monkeypatch.setattr(guard, "api", fake_api)
    monkeypatch.setattr(guard.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "output", lambda *args: pytest.fail("No authorized outputs"))
    with pytest.raises(ValueError, match="DEPLOYMENT_OPERATORS"):
        guard.main()


def test_ssh_and_scp_require_batch_strict_hosts_and_fixed_alias():
    import shlex
    command = kabre.ssh_argv(["python3", "-c", "print('x')", "/work/name with space"])
    assert command[:2] == ["ssh", "-T"]
    assert command[-2] == "kabre"
    assert shlex.split(command[-1]) == ["python3", "-c", "print('x')", "/work/name with space"]
    for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no"):
        assert option in command
        assert option in kabre.scp_argv("C:/tmp/payload.zip", "kabre:/work/test/payload.zip")


def test_remote_path_rejects_shell_and_parent_traversal(monkeypatch, tmp_path):
    for root in ("/work/x;touch /tmp/x", "/work/../home", "/", "relative"):
        monkeypatch.setenv("KABRE_CAMPAIGN_ROOT", root)
        with pytest.raises(ValueError):
            kabre.remote_path(tmp_path / "github-123-1")
    monkeypatch.setenv("KABRE_CAMPAIGN_ROOT", "/work/shidalgo/github-campaigns")
    assert kabre.remote_path(tmp_path / "github-123-1") == "/work/shidalgo/github-campaigns/github-123-1"


@pytest.mark.parametrize("changed", ["source_sha", "verification_run_id", "reference_artifact_id",
                                   "build_run_id", "build_run_attempt", "sof_sha256"])
def test_build_lineage_rejects_every_mismatched_identifier(monkeypatch, tmp_path, changed):
    binding = {"source_sha": SHA, "verification_run_id": "99", "reference_artifact_id": "88",
               "build_run_id": "77", "build_run_attempt": "1", "sof_sha256": DIGEST}
    for key, value in binding.items():
        monkeypatch.setenv(key.upper(), value)
    binding[changed] = "mismatch"
    (tmp_path / "deployment-binding.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match=changed):
        fpga.build_binding(tmp_path)


def test_remote_evidence_requires_selected_workflow_branch_and_allowed_operators(monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setattr(guard.subprocess, "run", lambda *args, **kwargs: None)
    run = {"event": "workflow_dispatch", "path": ".github/workflows/fpga.yml",
           "head_repository": {"full_name": REPO}, "head_branch": "master", "head_sha": SHA,
           "status": "completed", "conclusion": "success", "actor": {"login": "Katharsis0"},
           "triggering_actor": {"login": "Katharsis0"}}
    guard.validate_deployment_run(run, REPO, "Katharsis0")
    run["head_branch"] = "dev"
    with pytest.raises(ValueError, match="default branch"):
        guard.validate_deployment_run(run, REPO, "Katharsis0")
    run["head_branch"] = "master"
    run["triggering_actor"] = {"login": "other"}
    with pytest.raises(ValueError, match="triggering_actor"):
        guard.validate_deployment_run(run, REPO, "Katharsis0")


def test_zip_transfer_preserves_bytes_and_rejects_escape(tmp_path):
    import zipfile
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_bytes(b'{"x":1}\r\n')
    archive = tmp_path / "safe.zip"
    kabre.pack(source, archive)
    target = tmp_path / "target"
    kabre.unpack(archive, target)
    assert (target / "manifest.json").read_bytes() == b'{"x":1}\r\n'
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as handle:
        handle.writestr("../escape", "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        kabre.unpack(bad, tmp_path / "rejected")


@pytest.mark.parametrize("mode", ["preview", "execute"])
def test_local_plan_only_transfers_allowlisted_snapshot_with_explicit_execute(monkeypatch, tmp_path, mode):
    config_path, receipt_path = tmp_path / "config.json", tmp_path / "image.json"
    config = pilot_config()
    config["image"] = "/work/images/current.sif"  # Remote path must not be opened on Windows.
    config_path.write_text(json.dumps(config))
    receipt_path.write_text(json.dumps(image_receipt()))
    for key, value in {"SOURCE_SHA": SHA, "IMAGE_DIGEST": f"sha256:{DIGEST}", "KABRE_MODE": mode,
                       "KABRE_CONFIG": str(config_path), "KABRE_IMAGE_RECEIPT": str(receipt_path),
                       "KABRE_CAMPAIGN_ROOT": "/work/shidalgo/github-campaigns"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(kabre.subprocess, "check_output", lambda *args, **kwargs: SHA)
    campaign, evidence = tmp_path / "github-123-1", tmp_path / "evidence"
    calls = []
    def fake_command(argv, *args):
        calls.append(argv)
        if "plan" in argv:
            campaign.mkdir()
            (campaign / "manifest.json").write_text(json.dumps(
                {"source": {"git_sha": SHA, "dirty": False}, "config": config}))
            (campaign / "source").mkdir()
            (campaign / "source/source.py").write_text("# allowlisted source\n")
        return 0
    monkeypatch.setattr(kabre, "command", fake_command)
    kabre.run(campaign, evidence)
    if mode == "preview":
        assert len(calls) == 1
        assert json.loads((evidence / "preview.json").read_text())["remote_accessed"] is False
    else:
        assert [argv[0] for argv in calls[1:]] == ["ssh", "scp", "ssh", "ssh"]
        assert "remote-run" in calls[-1][-1]
        assert json.loads((evidence / "transfer.json").read_text())["ready"] is True
