#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only authorization for manually approved deployment workflows."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from urllib.parse import quote
from urllib.request import Request, urlopen


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def api(path: str) -> dict:
    repository_url = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}"
    request = Request(
        f"{repository_url}/{path}" if path else repository_url,
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_run(run: dict, sha: str, repository: str) -> None:
    require(run.get("event") == "push", "Reference run must be a trusted push, not PR/manual code.")
    require(run.get("path") == ".github/workflows/gatekeeper.yml", "Wrong reference workflow.")
    require(run.get("head_sha") == sha, "Reference run SHA differs from source_sha.")
    require(run.get("head_repository", {}).get("full_name", "").lower() == repository.lower(),
            "Reference run belongs to a different repository.")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Reference gatekeeper run must be completed and successful.")


def validate_operators(allowlist: str, actor: str, triggering_actor: str) -> None:
    operators = {name.lower() for name in re.split(r"[,\s]+", allowlist.strip()) if name}
    require(bool(operators) and all(re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) for name in operators),
            "DEPLOYMENT_OPERATORS must explicitly list GitHub operator logins.")
    require(actor.lower() in operators and triggering_actor.lower() in operators,
            "Both actor and triggering_actor must be approved deployment operators.")


def validate_deployment_run(run: dict, repository: str, allowlist: str) -> None:
    require(run.get("event") == "workflow_dispatch" and run.get("path") == ".github/workflows/fpga.yml",
            "Physical evidence must come from the manual FPGA workflow.")
    require(run.get("head_repository", {}).get("full_name", "").lower() == repository.lower(),
            "Deployment evidence belongs to another repository.")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Deployment evidence run must have completed successfully.")
    require(run.get("head_branch") == os.environ["GITHUB_REF"].removeprefix("refs/heads/"),
            "Deployment evidence must have been dispatched from the trusted default branch.")
    validate_operators(allowlist, run.get("actor", {}).get("login", ""),
                       run.get("triggering_actor", {}).get("login", ""))
    subprocess.run(["git", "merge-base", "--is-ancestor", run["head_sha"], os.environ["GITHUB_SHA"]], check=True)


def artifact_id(run_id: str, name: str) -> str:
    artifacts = []
    for page in range(1, 101):
        page_data = api(f"actions/runs/{run_id}/artifacts?per_page=100&page={page}")["artifacts"]
        artifacts.extend(page_data)
        if len(page_data) < 100:
            break
    candidates = [a for a in artifacts if a["name"] == name and not a["expired"]]
    require(len(candidates) == 1, f"Require exactly one unexpired {name} artifact.")
    return str(candidates[0]["id"])


def deployment_artifact(run_id: str, prefix: str) -> tuple[str, str]:
    require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), f"{prefix}: explicit run ID required.")
    run = api(f"actions/runs/{run_id}")
    validate_deployment_run(run, os.environ["GITHUB_REPOSITORY"], os.environ["DEPLOYMENT_OPERATORS"])
    attempt = str(run["run_attempt"])
    return artifact_id(run_id, f"{prefix}-{run_id}-{attempt}"), attempt


def output(name: str, value: str) -> None:
    require("\n" not in value and "\r" not in value, "Multiline output rejected.")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("fpga", "kabre"))
    args = parser.parse_args()
    sha = os.environ["SOURCE_SHA"]
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "source_sha requires a full lowercase commit SHA.")
    run_id = os.environ["VERIFICATION_RUN_ID"]
    require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), "verification_run_id must be a positive integer.")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Manual dispatch only.")
    validate_operators(os.environ.get("DEPLOYMENT_OPERATORS", ""), os.environ.get("GITHUB_ACTOR", ""),
                       os.environ.get("GITHUB_TRIGGERING_ACTOR", ""))
    repo = api("")
    default = repo["default_branch"]
    require(os.environ["GITHUB_REF"] == f"refs/heads/{default}", "Dispatch only from the default branch.")
    require(api(f"branches/{quote(default, safe='')}").get("protected") is True,
            "The default branch must be protected before privileged execution.")
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, os.environ["GITHUB_SHA"]], check=True)
    validate_run(api(f"actions/runs/{run_id}"), sha, os.environ["GITHUB_REPOSITORY"])
    output("source_sha", sha)
    if args.kind == "fpga":
        action = os.environ["DEPLOY_ACTION"]
        require(action in ("build", "program", "replay"), "Select build, program or replay.")
        output("reference_artifact_id", artifact_id(run_id, "junit-verification"))
        if action != "build":
            require(bool(re.fullmatch(r"[0-9a-f]{64}", os.environ.get("SOF_SHA256", ""))),
                    "Explicit reviewed SOF SHA256 required.")
            bundle, attempt = deployment_artifact(os.environ.get("BUILD_RUN_ID", ""), "fpga-build")
            output("bundle_id", bundle)
            output("build_attempt", attempt)
        if action == "replay":
            require(os.environ.get("KEY0_CONFIRMED") == "true", "Replay requires explicit post-program KEY0 confirmation.")
            require(bool(re.fullmatch(r"[0-9a-f]{64}", os.environ.get("PROGRAMMING_SHA256", ""))),
                    "Explicit reviewed programming evidence SHA256 required.")
            proof, attempt = deployment_artifact(os.environ.get("PROGRAMMING_RUN_ID", ""), "fpga-program-evidence")
            output("programming_id", proof)
            output("programming_attempt", attempt)
    else:
        require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", os.environ["IMAGE_DIGEST"])),
                "image_digest must be an immutable OCI sha256 digest.")
        require(os.environ.get("KABRE_MODE") in ("preview", "execute"), "Select bounded preview or execute.")


if __name__ == "__main__":
    main()
