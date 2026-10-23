#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Approved Windows -> strict SSH/SCP -> SLURM pilot; no remote runner/service."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

from deployment_guard import require

TEMPLATE = Path(__file__).resolve().parents[1]
CONTROLLER = TEMPLATE / "tools/hpc_campaign.py"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def validate_image(config: dict, receipt: dict, source_sha: str, image_digest: str) -> None:
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)), "An OCI digest is required.")
    require(receipt.get("source_sha") == source_sha, "SIF receipt belongs to an old/different source commit.")
    require(receipt.get("oci_digest") == image_digest, "SIF receipt does not identify the approved OCI digest.")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", str(config.get("image_sha256", "")))),
            "Kabré SIF SHA256 is mandatory, never null.")
    require(receipt.get("sif_sha256") == config["image_sha256"], "SIF checksum differs from conversion receipt.")
    require(config.get("partition", "").startswith(("kura", "dribe")), "Only approved CPU partitions are allowed.")
    # Deliberately narrower than the controller: this dispatch is a pilot, not a cohort campaign.
    for key, value in {"cpus": 1, "memory_mb": 2048, "time_minutes": 5,
                       "max_tasks": 1, "max_concurrency": 1, "max_cpus": 1,
                       "max_memory_mb": 2048, "max_time_minutes": 5}.items():
        require(type(config.get(key)) is int and config[key] == value, f"Pilot requires {key}={value}.")


def command(argv: list[str], evidence: Path, name: str, timeout: int) -> int:
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(argv, cwd=TEMPLATE, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        def text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        (evidence / f"{name}.json").write_text(
            json.dumps({"argv": argv, "returncode": None, "error": str(exc),
                        "stdout": text(getattr(exc, "stdout", None)),
                        "stderr": text(getattr(exc, "stderr", None))}, indent=2) + "\n", encoding="utf-8")
        raise
    (evidence / f"{name}.json").write_text(
        json.dumps({"argv": argv, "returncode": result.returncode,
                    "stdout": result.stdout, "stderr": result.stderr}, indent=2) + "\n", encoding="utf-8")
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def campaign_path() -> Path:
    root = Path(os.environ["RUNNER_TEMP"])
    require(root.is_absolute() and root.is_dir(), "RUNNER_TEMP must be an existing local directory.")
    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(bool(re.fullmatch(r"[1-9][0-9]*", run)) and bool(re.fullmatch(r"[1-9][0-9]*", attempt)),
            "Invalid GitHub run identity.")
    return root / f"github-{run}-{attempt}"


def remote_path(local: Path) -> str:
    root = os.environ["KABRE_CAMPAIGN_ROOT"].rstrip("/")
    require(bool(re.fullmatch(r"/[A-Za-z0-9_./-]+", root)) and
            ".." not in PurePosixPath(root).parts and root != "/",
            "KABRE_CAMPAIGN_ROOT requires an explicit remote absolute path without shell metacharacters.")
    require(bool(re.fullmatch(r"github-[1-9][0-9]*-[1-9][0-9]*", local.name)), "Invalid campaign directory.")
    return str(PurePosixPath(root) / local.name)


SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=15", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]


def ssh_argv(argv: list[str]) -> list[str]:
    # SSH executes a remote shell: quoting the whole remote argv is mandatory,
    # even though the local subprocess itself never uses shell=True.
    return ["ssh", "-T", *SSH_OPTIONS, "kabre", shlex.join(argv)]


def scp_argv(source: str, destination: str) -> list[str]:
    return ["scp", "-B", *SSH_OPTIONS, "--", source, destination]


def pack(directory: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(directory.rglob("*")):
            require(not path.is_symlink(), "Never transfer symlinks.")
            if path.is_file():
                output.write(path, path.relative_to(directory).as_posix())


def unpack(archive: Path, destination: Path) -> None:
    """Evidence retrieval only; never extract paths/links supplied outside the bundle."""
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        require(sum(i.file_size for i in source.infolist()) <= 256 * 1024 * 1024,
                "Evidence exceeds 256 MiB.")
        for item in source.infolist():
            parts = PurePosixPath(item.filename)
            target = (destination / item.filename).resolve()
            require(not parts.is_absolute() and ".." not in parts.parts and "\\" not in item.filename
                    and ":" not in item.filename and target.is_relative_to(destination.resolve())
                    and (item.external_attr >> 16) & 0o170000 != 0o120000,
                    "Unsafe ZIP entry.")
        source.extractall(destination)


# Only fixed stdlib code crosses the SSH shell; paths and digests are separate,
# quoted argv. Source payload is a bounded hpc_campaign allowlist, never .git/SSH.
RESERVE = (
    "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
    "assert p.parent.is_dir(), 'shared campaign root missing'; "
    "p.mkdir(mode=0o700,exist_ok=False)"
)
EXTRACT = (
    "import hashlib,pathlib,sys,zipfile; p=pathlib.Path(sys.argv[1]); "
    "z=p/'payload.zip'; assert hashlib.sha256(z.read_bytes()).hexdigest()==sys.argv[2], 'payload hash mismatch'; "
    "f=zipfile.ZipFile(z); items=f.infolist(); "
    "assert sum(i.file_size for i in items)<=80*1024*1024, 'payload too large'; "
    "assert all(not pathlib.PurePosixPath(i.filename).is_absolute() "
    "and '..' not in pathlib.PurePosixPath(i.filename).parts "
    "and chr(92) not in i.filename and ':' not in i.filename "
    "and ((i.external_attr>>16)&0o170000)!=0o120000 for i in items), 'unsafe payload'; "
    "f.extractall(p)"
)


def remote_command(action: str, campaign: str, manifest_hash: str) -> list[str]:
    return ssh_argv(["python3", f"{campaign}/source/tools/kabre_deploy.py", action,
                     "--campaign", campaign, "--evidence", f"{campaign}/deployment-evidence",
                     "--source-sha", os.environ["SOURCE_SHA"], "--image-digest", os.environ["IMAGE_DIGEST"],
                     "--manifest-sha256", manifest_hash])


def cleanup(campaign: Path, evidence: Path) -> None:
    receipt_path = campaign / "submission.json"
    if not receipt_path.exists():
        require(not (campaign / "submission-claim.json").exists(),
                "Submission claim without receipt: inspect SLURM before retrying; no guessed cancellation.")
        print("No submission receipt: nothing confirmed for cancellation.")
        return
    receipt = load(receipt_path)
    job = receipt.get("job_id", "")
    require(bool(re.fullmatch(r"[0-9]+", job)), "Submission ambiguous: operator must inspect SLURM; no guessed cancellation.")
    require(receipt.get("manifest_sha256") == sha256(campaign / "manifest.json"),
            "Receipt/manifest mismatch; refusing to cancel an unbound job.")
    summary = load(campaign / "summary.json") if (campaign / "summary.json").exists() else {}
    if summary.get("complete") is True:
        print(f"Job {job} has terminal accounting; no cancellation needed.")
        return
    argv = ["scancel"]
    if receipt.get("cluster"):
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+", receipt["cluster"])), "Invalid SLURM cluster name.")
        argv += ["--clusters", receipt["cluster"]]
    argv += [job]
    require(command(argv, evidence, "cancel", 30) == 0, "scancel failed: operator intervention required.")


def export_evidence(campaign: Path, evidence: Path) -> None:
    """Export only this campaign's reports, not its build trees or other users' results."""
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "submission.json", "submission-claim.json", "summary.json", "image-receipt.json"):
        if (campaign / name).is_file():
            shutil.copyfile(campaign / name, evidence / name)
    for name in ("accounting", "logs"):
        if (campaign / name).is_dir():
            shutil.copytree(campaign / name, evidence / name, dirs_exist_ok=True)
    if not (campaign / "submission.json").exists():
        return
    receipt = load(campaign / "submission.json")
    if not re.fullmatch(r"[0-9]+", receipt.get("job_id", "")):
        return
    manifest = load(campaign / "manifest.json")
    root = Path(manifest["config"]["results_root"]) / receipt["job_id"]
    if root.is_dir():
        for task in root.glob("*/attempt-*"):
            relative = task.relative_to(root)
            for name in ("task.json",):
                if (task / name).is_file():
                    target = evidence / "tasks" / relative / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(task / name, target)
            for name in ("results", "logs"):
                if (task / name).is_dir():
                    shutil.copytree(task / name, evidence / "tasks" / relative / name, dirs_exist_ok=True)


def run(campaign: Path, evidence: Path) -> None:
    """Plan locally, transfer only allowlisted sources, run existing controller remotely."""
    source_sha = os.environ["SOURCE_SHA"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=TEMPLATE, text=True).strip()
    require(head == source_sha, "Checkout SHA differs from the approved commit.")
    config_path, receipt_path = Path(os.environ["KABRE_CONFIG"]), Path(os.environ["KABRE_IMAGE_RECEIPT"])
    require(config_path.is_absolute() and receipt_path.is_absolute(), "Config and conversion receipt need absolute paths.")
    config, receipt = load(config_path), load(receipt_path)
    validate_image(config, receipt, source_sha, os.environ["IMAGE_DIGEST"])
    # image/results_root in config are Linux shared paths; never hash that path
    # on Windows. The remote adapter and compute worker verify the SIF bytes.
    require(not campaign.exists(), "Campaign already exists; never retry an uncertain submission.")
    rc = command([sys.executable, str(CONTROLLER), "plan", "--config", str(config_path),
                  "--source", str(TEMPLATE), "--output", str(campaign), "--mode", "demo",
                  "--batch-size", "1", "--repetitions", "1", "--concurrency", "1",
                  "--seed", "1234", "--cycles", "200"], evidence, "plan-command", 60)
    require(rc == 0, "Campaign planning failed.")
    shutil.copyfile(receipt_path, campaign / "image-receipt.json")
    manifest = campaign / "manifest.json"
    planned = load(manifest)
    require(planned["source"]["git_sha"] == source_sha and planned["source"]["dirty"] is False,
            "Pilot snapshot is dirty or bound to a different SHA.")
    mode = os.environ["KABRE_MODE"]
    require(mode in ("preview", "execute"), "Explicit preview or execute selection required.")
    if mode == "preview":
        export_evidence(campaign, evidence)
        (evidence / "preview.json").write_text(
            json.dumps({"status": "PREVIEW_ONLY", "submitted": False, "remote_accessed": False,
                        "source_sha": source_sha, "manifest_sha256": sha256(manifest)}, indent=2), encoding="utf-8")
        print("PREVIEW_ONLY: no SSH, upload, submission or measured pilot success.")
        return
    target = remote_path(campaign)
    archive = campaign.parent / f"{campaign.name}-payload.zip"
    pack(campaign, archive)
    evidence.mkdir(parents=True, exist_ok=True)
    state = {"campaign": target, "manifest_sha256": sha256(manifest), "ready": False}
    state_path = evidence / "transfer.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    require(command(ssh_argv(["python3", "-I", "-c", RESERVE, target]), evidence, "reserve", 45) == 0,
            "Remote campaign reservation failed; no automatic overwrite/retry.")
    require(command(scp_argv(str(archive), f"kabre:{target}/payload.zip"),
                    evidence, "upload", 180) == 0, "SCP source upload failed.")
    require(command(ssh_argv(["python3", "-I", "-c", EXTRACT, target, sha256(archive)]),
                    evidence, "extract", 60) == 0, "Remote payload extraction/hash validation failed.")
    state["ready"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    require(command(remote_command("remote-run", target, state["manifest_sha256"]),
                    evidence, "remote-run", 780) == 0,
            "Remote pilot failed, timed out or lacks required evidence; cleanup/retrieval remains mandatory.")


def run_remote(campaign: Path, evidence: Path, source_sha: str, image_digest: str, manifest_hash: str) -> None:
    require(sys.platform.startswith("linux"), "Remote controller requires Linux.")
    manifest = campaign / "manifest.json"
    require(sha256(manifest) == manifest_hash, "Transferred manifest hash mismatch.")
    planned = load(manifest)
    config = planned["config"]
    validate_image(config, load(campaign / "image-receipt.json"), source_sha, image_digest)
    require(planned["source"]["git_sha"] == source_sha and planned["source"]["dirty"] is False,
            "Transferred snapshot SHA/cleanliness mismatch.")
    require(sha256(Path(config["image"])) == config["image_sha256"], "Remote SIF bytes fail SHA256.")
    controller = campaign / "source/tools/hpc_campaign.py"
    require(command([sys.executable, str(controller), "submit", "--manifest", str(manifest),
                     "--require-idle", "--execute"], evidence, "submit-command", 90) == 0, "SLURM submission failed.")
    require(command([sys.executable, str(controller), "wait", "--manifest", str(manifest),
                     "--timeout-seconds", "600", "--poll-seconds", "15"],
                    evidence, "wait-command", 660) == 0, "Pilot failed, timed out or lacks required evidence.")
    summary = load(campaign / "summary.json")
    require(summary.get("complete") is True and summary.get("verdict") == "PASS", "No complete PASS evidence.")


def cleanup_local(campaign: Path, evidence: Path) -> None:
    state_path = evidence / "transfer.json"
    if not state_path.exists() or load(state_path).get("ready") is not True:
        print("Remote controller was not started; no remote job cancellation is authorized.")
        return
    state = load(state_path)
    target = remote_path(campaign)
    require(state["campaign"] == target and state["manifest_sha256"] == sha256(campaign / "manifest.json"),
            "Transport state differs from this exact local campaign.")
    code = command(remote_command("remote-cleanup", target, state["manifest_sha256"]),
                   evidence, "remote-cleanup", 120)
    archive = evidence / "remote-evidence.zip"
    require(command(scp_argv(f"kabre:{target}/evidence.zip", str(archive)),
                    evidence, "download", 180) == 0, "Remote evidence download failed.")
    unpack(archive, evidence / "remote")
    require(code == 0, "Remote cleanup failed; downloaded diagnostics require operator inspection.")
    summary_path = evidence / "remote/summary.json"
    require(summary_path.is_file(), "Mandatory remote summary absent.")
    summary = load(summary_path)
    require(summary.get("complete") is True and summary.get("verdict") == "PASS",
            "Retrieved campaign evidence is not complete PASS.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "cleanup", "remote-run", "remote-cleanup"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--image-digest")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    remote = args.action.startswith("remote-")
    campaign = args.campaign if remote else campaign_path()
    require(campaign is not None, "Remote operation requires the transferred campaign path.")
    if remote:
        require(args.manifest_sha256 is not None and
                sha256(campaign / "manifest.json") == args.manifest_sha256,
                "Remote operation requires the exact approved manifest hash.")
    if args.action.endswith("cleanup"):
        try:
            if remote:
                cleanup(campaign, args.evidence)
            else:
                cleanup_local(campaign, args.evidence)
        finally:
            if remote:
                export_evidence(campaign, args.evidence)
                pack(args.evidence, campaign / "evidence.zip")
        return
    preview = not remote and os.environ.get("KABRE_MODE") == "preview"
    suite = ET.Element("testsuite", name="kabre-preview" if preview else "kabre-pilot", tests="1", failures="0")
    case = ET.SubElement(suite, "testcase", name="plan-only-no-remote" if preview else "bounded-current-image-pilot")
    try:
        if remote:
            run_remote(campaign, args.evidence, args.source_sha, args.image_digest, args.manifest_sha256)
        else:
            run(campaign, args.evidence)
    except BaseException as exc:
        suite.set("failures", "1")
        ET.SubElement(case, "failure", message=str(exc)).text = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if remote:
            export_evidence(campaign, args.evidence)
        args.evidence.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(args.evidence / "junit.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
