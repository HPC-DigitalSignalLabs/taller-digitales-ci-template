# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Offline contract tests: authorization scope, bounded data, CI and snapshots."""

from __future__ import annotations

import copy
import io
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import cohort  # noqa: E402
import dashboard  # noqa: E402

REPO = "curso/equipo-01"
BASE = "/repos/" + REPO
SHA = "a" * 40
DATE = "2026-09-24T12:00:00Z"
NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
NEXT = {"Link": '<https://api.github.com/unused?page=2>; rel="next"'}


@pytest.fixture
def config():
    return cohort.validate_config({
        "schema_version": 1, "cohort": "Prueba",
        "allowed_owners": ["curso"], "repositories": [REPO],
        "repository_name_regex": "^equipo-[0-9]{2}$",
        "collection": {},
        "ci": {"workflow_file": "gatekeeper.yml", "required_jobs": ["Gatekeeper verdict"]},
    })


@pytest.fixture
def workspace():
    # Deliberately stay inside the project, never in the OS temporary directory.
    path = Path(__file__).parent / (".cohort-test-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def activity(number=1, **changes):
    return {"number": number, "state": "open", "created_at": DATE, "updated_at": DATE,
            "user": {"login": "ana"}, **changes}


def run(**changes):
    return {
        "id": 11, "run_number": 1, "run_attempt": 1, "workflow_id": 7,
        "event": "push", "head_branch": "main", "head_sha": SHA,
        "head_repository": {"full_name": REPO}, "status": "completed",
        "updated_at": DATE, **changes,
    }


def job(**changes):
    return {"name": "Gatekeeper verdict", "head_sha": SHA,
            "status": "completed", "conclusion": "success", **changes}


class FakeApi:
    def __init__(self, routes=None):
        self.calls = []
        self.routes = {
            BASE: ({"full_name": REPO, "default_branch": "main"}, {}),
            BASE + "/commits": ([{"sha": SHA, "author": {"login": "ana"},
                                 "commit": {"committer": {"date": DATE}}}], {}),
            BASE + "/issues": ([activity(), activity(2, pull_request={})], {}),
            BASE + "/pulls": ([activity(2, state="closed", merged_at=DATE)], {}),
            BASE + "/pulls/2/reviews": ([
                {"id": 21, "state": "APPROVED", "user": {"login": "docente"},
                 "submitted_at": DATE, "body": "PRIVATE TEXT MUST NOT BE SAVED"},
                {"id": 22, "state": "PENDING", "user": {"login": "docente"},
                 "submitted_at": None},
            ], {}),
            BASE + "/actions/workflows/gatekeeper.yml": (
                {"id": 7, "path": ".github/workflows/gatekeeper.yml"}, {}),
            BASE + "/actions/workflows/7/runs": ({"workflow_runs": [run()], "total_count": 1}, {}),
            BASE + "/actions/runs/11/attempts/1/jobs": ({"jobs": [job()], "total_count": 1}, {}),
        }
        self.routes.update(routes or {})

    def get_json_with_headers(self, path, params=None):
        self.calls.append((path, params))
        response = self.routes.get((path, (params or {}).get("page")), self.routes.get(path))
        if response is None:
            raise AssertionError(f"Unscripted API call: {path}")
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def collect(config, api=None):
    return cohort.Collector(api or FakeApi(), config, dashboard.DashboardError).collect([REPO], NOW)


def test_complete_snapshot_context_and_no_raw_private_text(config):
    snapshot = collect(config)
    result = snapshot["repositories"][0]
    assert result["ci"]["items"][0]["verdict"] == "verde"
    assert result["ci"]["identity"]["workflow_id"] == 7
    assert len(result["issues"]["items"]) == 1
    assert len(result["pull_requests"]["items"]) == 1
    assert len(result["reviews"]["items"]) == 1
    assert not cohort.incomplete(snapshot)
    report = cohort.render(snapshot)
    assert "100.0%" in report
    assert "docente" in report and "ana" in report
    assert "(merged)" in report
    assert "no esfuerzo, calidad ni nota" in report
    assert "PRIVATE TEXT" not in json.dumps(snapshot)


@pytest.mark.parametrize("changes", [
    {"allowed_owners": []}, {"allowed_owners": ["otro"]},
    {"repositories": ["curso/equipo-01", "CURSO/EQUIPO-01"]},
    {"repositories": ["curso/equipo-01/../../otro"]},
    {"repositories": ["curso/equipo-01-extra"]},
    {"repository_name_regex": "equipo"}, {"repository_name_regex": "^[$"},
    {"collection": {"max_requests": 0}}, {"collection": {"max_requests": True}},
    {"collection": {"typo_limit": 100}}, {"cohort": ""},
    {"ci": {"workflow_file": "../evil.yml", "required_jobs": ["gatekeeper"]}},
    {"ci": {"workflow_file": "gatekeeper.yml", "required_jobs": []}},
    {"token": "must-not-be-in-config"},
])
def test_invalid_config_fails_closed(config, changes):
    config.update(changes)
    with pytest.raises(cohort.CohortError):
        cohort.validate_config(config)


def test_selection_and_matrix_are_allowlist_not_discovery(config):
    config["allowed_owners"].append("personal")
    config["repositories"].append("personal/equipo-02")
    assert cohort.selected_repos(config, "CURSO") == [REPO]
    assert cohort.installation_matrix(config) == {"include": [
        {"owner": "curso", "repositories": "equipo-01"},
        {"owner": "personal", "repositories": "equipo-02"},
    ]}
    with pytest.raises(cohort.CohortError):
        cohort.selected_repos(config, "attacker")
    api = FakeApi()
    collect(config, api)
    assert all(path.startswith(BASE) for path, _ in api.calls)


def test_pagination_follows_pages_without_following_link_url(config):
    api = FakeApi({
        ("/list", 1): ([{"id": 1}], NEXT),
        ("/list", 2): ([{"id": 2}], {}),
    })
    collector = cohort.Collector(api, config, dashboard.DashboardError)
    result = collector.pages("/list", 100)
    assert result["status"] == "completo"
    assert [i["id"] for i in result["items"]] == [1, 2]
    assert api.calls == [
        ("/list", {"per_page": 100, "page": 1}),
        ("/list", {"per_page": 100, "page": 2}),
    ]


def test_page_failure_preserves_partial_records(config):
    api = FakeApi({("/list", 1): ([{"id": 1}], NEXT),
                   ("/list", 2): dashboard.DashboardError("HTTP 500")})
    result = cohort.Collector(api, config, dashboard.DashboardError).pages("/list", 100)
    assert result["status"] == "parcial"
    assert cohort.count_label(result) == ">=1 (parcial)"
    assert result["errors"] == ["HTTP 500"]


def test_exact_object_total_is_complete_at_item_cap(config):
    api = FakeApi({"/list": ({"jobs": [job()], "total_count": 1}, {})})
    result = cohort.Collector(api, config, dashboard.DashboardError).pages("/list", 1, key="jobs")
    assert result["status"] == "completo"
    assert len(api.calls) == 1


@pytest.mark.parametrize("payload", [{}, {"wrong_key": []}, ["invalid"], None])
def test_malformed_payload_is_not_zero(config, payload):
    api = FakeApi({"/list": (payload, {})})
    result = cohort.Collector(api, config, dashboard.DashboardError).pages("/list", 100)
    assert result["status"] == "error"
    assert cohort.count_label(result) == "N/D"


def test_budget_stops_all_network_and_reports_unknown(config):
    config["collection"]["max_requests"] = 2
    api = FakeApi()
    snapshot = collect(config, api)
    assert snapshot["api_requests"] == len(api.calls) == 2
    result = snapshot["repositories"][0]
    assert result["commits"]["status"] == "completo"
    assert result["issues"]["status"] == "error"
    assert result["reviews"]["status"] == "desconocido"
    assert "N/D" in cohort.render(snapshot)
    assert cohort.incomplete(snapshot)


@pytest.mark.parametrize("message", ["GITHUB_TOKEN is expired", "GitHub API rate limit reached"])
def test_auth_or_rate_limit_failure_stops_further_calls(config, message):
    api = FakeApi({BASE + "/commits": dashboard.DashboardError(message)})
    snapshot = collect(config, api)
    assert len(api.calls) == 2
    assert snapshot["repositories"][0]["commits"]["status"] == "error"


def test_remaining_zero_finishes_current_response_then_stops(config):
    api = FakeApi({BASE: ({"full_name": REPO, "default_branch": "main"},
                          {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "100"})})
    snapshot = collect(config, api)
    assert len(api.calls) == 1
    assert snapshot["repositories"][0]["commits"]["status"] == "error"


def test_partial_runs_do_not_reuse_old_green_or_claim_no_runs(config):
    config["collection"]["max_workflow_runs"] = 1
    api = FakeApi({BASE + "/actions/workflows/7/runs":
                   ({"workflow_runs": [run()], "total_count": 2}, NEXT)})
    snapshot = collect(config, api)
    result = snapshot["repositories"][0]["ci"]
    assert result["status"] == "parcial"
    assert result["items"][0]["verdict"] == "desconocido"
    assert not any("/jobs" in path for path, _ in api.calls)


def test_latest_attempt_failure_overrides_historical_success(config):
    api = FakeApi({
        BASE + "/actions/workflows/7/runs": (
            {"workflow_runs": [run(), run(run_attempt=2)], "total_count": 2}, {}),
        BASE + "/actions/runs/11/attempts/2/jobs": (
            {"jobs": [job(conclusion="failure")], "total_count": 1}, {}),
    })
    ci = collect(config, api)["repositories"][0]["ci"]
    assert ci["items"][0]["verdict"] == "rojo"
    assert ci["items"][0]["attempt"] == 2
    assert not any("/attempts/1/" in path for path, _ in api.calls)


@pytest.mark.parametrize("changes", [
    {"event": "pull_request"}, {"workflow_id": 999}, {"head_branch": "dev"},
    {"head_repository": {"full_name": "otro/equipo-01"}},
])
def test_wrong_run_identity_never_green(config, changes):
    api = FakeApi({BASE + "/actions/workflows/7/runs":
                   ({"workflow_runs": [run(**changes)], "total_count": 1}, {})})
    ci = collect(config, api)["repositories"][0]["ci"]
    assert ci["status"] == "parcial"
    assert ci["items"][0]["verdict"] == "desconocido"


def test_wrong_workflow_path_is_error(config):
    api = FakeApi({BASE + "/actions/workflows/gatekeeper.yml":
                   ({"id": 7, "path": ".github/workflows/not-gatekeeper.yml"}, {})})
    assert collect(config, api)["repositories"][0]["ci"]["status"] == "error"


@pytest.mark.parametrize(("jobs", "expected"), [
    ([job()], "verde"),
    ([job(name="fake Gatekeeper verdict")], "desconocido"),
    ([job(head_sha="b" * 40)], "desconocido"),
    ([job(), job()], "desconocido"),
    ([job(conclusion="skipped")], "desconocido"),
    ([job(conclusion="neutral")], "desconocido"),
    ([job(conclusion=None, status="in_progress")], "pendiente"),
    ([job(conclusion="cancelled")], "rojo"),
    ([job(conclusion="timed_out")], "rojo"),
])
def test_exact_jobs_conservative_verdict(jobs, expected):
    result = cohort.jobs_verdict(cohort.section("/jobs", items=jobs), ["Gatekeeper verdict"], SHA, "completed")
    assert result == expected


def test_all_required_jobs_must_pass():
    jobs = cohort.section("/jobs", items=[job(), job(name="Smoke", conclusion="failure")])
    assert cohort.jobs_verdict(jobs, ["Smoke", "Gatekeeper verdict"], SHA, "completed") == "rojo"
    assert cohort.jobs_verdict(jobs, ["Missing", "Gatekeeper verdict"], SHA, "completed") == "desconocido"


def test_no_runs_is_distinct_from_api_failure(config):
    empty = FakeApi({BASE + "/actions/workflows/7/runs":
                     ({"workflow_runs": [], "total_count": 0}, {})})
    result = collect(config, empty)["repositories"][0]
    assert result["ci"]["items"][0]["verdict"] == "sin_ejecuciones"
    failed = FakeApi({BASE + "/actions/workflows/7/runs": dashboard.DashboardError("HTTP 403")})
    result = collect(config, failed)["repositories"][0]
    assert result["ci"]["items"][0]["verdict"] == "desconocido"
    assert result["ci"]["status"] == "error"


def test_missing_reviews_are_not_zero(config):
    api = FakeApi({BASE + "/pulls/2/reviews": dashboard.DashboardError("HTTP 404")})
    result = collect(config, api)["repositories"][0]
    assert result["reviews"]["status"] == "error"
    assert cohort.count_label(result["reviews"]) == "N/D"


def test_malformed_activity_is_error_not_silently_ignored(config):
    api = FakeApi({BASE + "/issues": ([{"number": 1}], {})})
    assert collect(config, api)["repositories"][0]["issues"]["status"] == "error"


def test_pr_list_stops_at_old_updates_and_excludes_future_data(config):
    api = FakeApi({BASE + "/pulls": ([
        activity(3, updated_at="2026-09-26T00:00:00Z"),
        activity(2),
        activity(4, updated_at="2026-08-01T00:00:00Z"),
    ], NEXT)})
    result = collect(config, api)["repositories"][0]
    assert [pr["number"] for pr in result["pull_requests"]["items"]] == [2]
    assert result["pull_requests"]["status"] == "completo"
    assert not any(path == BASE + "/pulls" and params["page"] > 1 for path, params in api.calls)


def test_snapshot_reuse_preserves_timestamp_and_scope(config, workspace):
    snapshot = collect(config)
    path = workspace / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    reused = cohort.read_snapshot(path, config, [REPO])
    assert reused == snapshot
    assert "sin consultas ni actualización" in cohort.render(reused, reused=True)
    changed = copy.deepcopy(config)
    changed["ci"]["required_jobs"] = ["Other"]
    with pytest.raises(cohort.CohortError):
        cohort.read_snapshot(path, changed, [REPO])
    with pytest.raises(cohort.CohortError):
        cohort.read_snapshot(path, config, ["curso/equipo-02"])


@pytest.mark.parametrize("metric, item", [
    ("commits", {}), ("ci", {"sha": SHA, "verdict": "invented", "run_id": 1}),
    ("issues", {"author": "ana"}), ("reviews", {"author": "ana", "pr": 1}),
])
def test_invalid_snapshot_records_rejected_before_render(config, workspace, metric, item):
    snapshot = collect(config)
    snapshot["repositories"][0][metric]["items"] = [item]
    path = workspace / "invalid.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(cohort.CohortError):
        cohort.read_snapshot(path, config, [REPO])


def test_all_api_failures_never_render_total_zero(config):
    api = FakeApi({BASE: dashboard.DashboardError("HTTP 404")})
    report = cohort.render(collect(config, api))
    assert "Veredictos observados en la cohorte: N/D" in report
    assert "0 verdes" not in report


def test_markdown_escapes_untrusted_activity(config):
    api = FakeApi()
    api.routes[BASE + "/commits"][0][0]["author"]["login"] = "<script>|bad`\n"
    report = cohort.render(collect(config, api))
    assert "<script>" not in report
    assert "&lt;script&gt;&#124;bad&#96;" in report


def test_cli_collect_persist_and_replay_without_token(config, workspace, monkeypatch, capsys):
    config_path = workspace / "cohort.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = workspace / "dashboard.md"
    monkeypatch.setenv("GITHUB_TOKEN", "test-value-never-written")
    monkeypatch.setattr(dashboard, "GitHubApi", lambda token: FakeApi())
    monkeypatch.setattr(cohort.Collector, "collect",
                        lambda self, repos: ORIGINAL_COLLECT(self, repos, NOW))
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--config", str(config_path),
                                    "--output", str(output)])
    assert dashboard.main() == 0
    assert output.exists() and output.with_suffix(".json").exists()
    assert "test-value-never-written" not in output.with_suffix(".json").read_text()
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setattr(dashboard, "GitHubApi", lambda token: pytest.fail("offline must not use API"))
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--config", str(config_path),
                                    "--from-snapshot", str(output.with_suffix(".json")),
                                    "--output", str(workspace / "replay.md")])
    assert dashboard.main() == 0
    assert "snapshot reutilizado" in (workspace / "replay.md").read_text(encoding="utf-8")
    assert "test-value-never-written" not in capsys.readouterr().out


ORIGINAL_COLLECT = cohort.Collector.collect


def test_cli_partial_snapshot_persists_and_returns_three(config, workspace, monkeypatch):
    config["collection"]["max_requests"] = 1
    config_path = workspace / "cohort.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = workspace / "dashboard.md"
    monkeypatch.setenv("GITHUB_TOKEN", "test-only")
    monkeypatch.setattr(dashboard, "GitHubApi", lambda token: FakeApi())
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--config", str(config_path),
                                    "--output", str(output)])
    assert dashboard.main() == 3
    assert output.exists() and output.with_suffix(".json").exists()


def test_cli_config_from_env_validates_without_token(config, workspace, monkeypatch):
    path = workspace / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_CONFIG", str(path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--validate-config"])
    assert dashboard.main() == 0


def test_cli_missing_token_is_explicit_without_network(config, workspace, monkeypatch, capsys):
    path = workspace / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--config", str(path)])
    monkeypatch.setattr(dashboard, "GitHubApi", lambda *args: pytest.fail("token required first"))
    assert dashboard.main() == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_cli_org_without_allowlist_does_not_discover_repos(monkeypatch, capsys):
    monkeypatch.delenv("DASHBOARD_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--org", "curso"])
    monkeypatch.setattr(dashboard, "GitHubApi", lambda *args: pytest.fail("no discovery"))
    assert dashboard.main() == 2
    assert "allowlist" in capsys.readouterr().err


@pytest.mark.parametrize("code", [401, 403, 404, 409, 422, 429, 500])
def test_http_errors_never_contain_response_body_or_token(code):
    api = dashboard.GitHubApi("credential-never-log")
    exc = HTTPError("https://api.github.com", code, "failed", {}, io.BytesIO(b"private body"))
    with pytest.raises(dashboard.DashboardError) as error:
        api._raise_http_error("/repos/curso/equipo-01", exc, "private body")
    assert "private body" not in str(error.value)
    assert "credential-never-log" not in str(error.value)


def test_secondary_rate_limit_returns_retry_hint():
    api = dashboard.GitHubApi("test-only")
    exc = HTTPError("https://api.github.com", 403, "failed", {"Retry-After": "60"}, None)
    with pytest.raises(dashboard.DashboardError, match="rate limit.*60"):
        api._raise_http_error("/repos/curso/equipo-01", exc, "private body")


def test_redirects_are_not_followed():
    assert dashboard.NoRedirect().redirect_request(None, None, 301, "", {}, "https://other") is None


def test_api_uses_timeout_and_redacts_network_error(monkeypatch):
    class Opener:
        def open(self, req, timeout):
            assert timeout == 30
            raise URLError("offline")
    monkeypatch.setattr(dashboard.request, "build_opener", lambda *args: Opener())
    with pytest.raises(dashboard.DashboardError, match="Cannot reach"):
        dashboard.GitHubApi("test-only").get_json("/repos/curso/equipo-01")


def test_workflow_opt_in_private_and_read_only_contract():
    root = Path(__file__).resolve().parents[3]
    path = root / ".github" / "workflows" / "dashboard.yml"
    if not path.is_file():
        # The collector workflow belongs to the teacher repository, not the
        # portable verification payload mounted as /work by the gatekeeper.
        # Native smoke validates this contract from the complete checkout.
        pytest.skip("Workflow docente externo a template; validar desde checkout completo.")
    workflow = path.read_text(encoding="utf-8")
    assert "DASHBOARD_ENABLED == 'true'" in workflow
    assert "--jq '.private'" in workflow
    assert "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349" in workflow
    assert "permission-administration" not in workflow
    assert "DASHBOARD_TOKEN" not in workflow
    assert "retention-days: 14" in workflow
    assert "workflow_dispatch:" in workflow and "schedule:" in workflow
    assert "pull_request:" not in workflow
