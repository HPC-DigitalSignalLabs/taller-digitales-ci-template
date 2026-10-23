# SPDX-License-Identifier: Apache-2.0
"""Deterministic demonstration through the production snapshot CLI, never network."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cohort
import cohort_demo
import dashboard


def test_fixture_deterministic_and_explicit(tmp_path):
    config, snapshot = cohort_demo.fixtures()
    assert (config, snapshot) == cohort_demo.fixtures()
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    restored = cohort.read_snapshot(path, config, config["repositories"])
    report = cohort.render(restored, reused=True)
    assert "DEMO NO LIVE" in report
    assert "REST GitHub" not in report
    assert "1 verdes, 1 rojos" in report
    assert "| demo-no-live/equipo-03 | N/D | N/D | N/D | N/D | N/D | N/D |" in report
    assert restored["api_requests"] == 0
    assert cohort.incomplete(restored)


def test_replay_cli_propagates_partial_without_network(tmp_path, monkeypatch):
    config, snapshot = cohort_demo.fixtures()
    for name, value in (("config.json", config), ("snapshot.json", snapshot)):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    def forbidden():
        pytest.fail("Offline replay must not request credentials.")
    monkeypatch.setattr(dashboard, "require_token", forbidden)
    output = tmp_path / "dashboard.md"
    monkeypatch.setattr(sys, "argv", [
        "dashboard", "--config", str(tmp_path / "config.json"),
        "--from-snapshot", str(tmp_path / "snapshot.json"), "--output", str(output),
    ])
    assert dashboard.main() == 3
    assert "DEMO NO LIVE" in output.read_text(encoding="utf-8")


def test_generator_cli_and_help(tmp_path):
    script = Path(cohort_demo.__file__)
    for args in (["--help"], ["--output", str(tmp_path)]):
        result = subprocess.run([sys.executable, str(script), *args],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    config = cohort.load_config(tmp_path / "config.json")
    cohort.read_snapshot(tmp_path / "snapshot.json", config, config["repositories"])
