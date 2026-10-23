#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create deterministic NON-LIVE dashboard fixtures without credentials or network."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cohort

DATE = "2026-10-05T12:00:00+00:00"
SINCE = "2026-09-21T12:00:00+00:00"


def fixtures() -> tuple[dict, dict]:
    """Three invented teams: green, red and unavailable (never false zero/green)."""
    config = cohort.validate_config({
        "schema_version": 1, "cohort": "DEMO NO LIVE — equipos inventados",
        "allowed_owners": ["demo-no-live"],
        "repositories": [f"demo-no-live/equipo-{i:02}" for i in range(1, 4)],
        "repository_name_regex": "^equipo-[0-9]{2}$",
        "collection": {"max_requests": 1},
        "ci": {"workflow_file": "gatekeeper.yml", "required_jobs": ["Gatekeeper verdict"]},
    })
    snapshot = {
        "schema_version": 1, "cohort": config["cohort"],
        "provenance": "synthetic-fixture",
        "policy_hash": cohort.policy_hash(config, config["repositories"]),
        "generated_at": DATE, "window": {"since": SINCE, "until": DATE},
        "api_requests": 0, "request_budget": 1, "repositories": [],
    }
    for index, name in enumerate(config["repositories"], 1):
        unavailable = index == 3
        sha = str(index) * 40
        repo = {"repository": name, "default_branch": "main"}
        for key in ("commits", "issues", "pull_requests", "reviews", "ci"):
            repo[key] = {
                "status": "error" if unavailable else "completo", "items": [],
                "source": f"fixture://demo-no-live/{index}/{key}", "observed_at": DATE,
                "errors": ["Fallo API simulado; datos desconocidos."] if unavailable else [],
            }
        if not unavailable:
            repo["commits"]["items"] = [{"sha": sha, "author": f"persona-demo-{index}", "date": DATE}]
            repo["ci"]["items"] = [{
                "sha": sha, "verdict": "verde" if index == 1 else "rojo",
                "run_id": index, "attempt": 1, "updated_at": DATE,
            }]
            repo["issues"]["items"] = [{
                "number": index, "state": "open", "author": f"persona-demo-{index}",
                "created_at": DATE, "updated_at": DATE,
            }]
        snapshot["repositories"].append(repo)
    return config, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Dedicated local directory for config.json and snapshot.json.")
    args = parser.parse_args()
    config, snapshot = fixtures()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in (("config.json", config), ("snapshot.json", snapshot)):
        (args.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DEMO NO LIVE: {args.output}; 0 network requests. Replay with dashboard.py --from-snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
