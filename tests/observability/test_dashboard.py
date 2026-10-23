# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Pure, network-free tests for the static cohort dashboard (ADR-0007).

Every HTTP boundary is replaced by an in-memory fake, so these tests are
deterministic and never touch GitHub. They pin the metric math (acceptance
rate), code-file classification, gatekeeper reduction, Link-header pagination
and the Markdown rendering contract that TAs and teachers read.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import dashboard  # noqa: E402


# --------------------------------------------------------------------------- #
# acceptance_rate math
# --------------------------------------------------------------------------- #
def test_acceptance_rate_basic_ratio() -> None:
    assert dashboard.compute_acceptance_rate(3, 1) == 0.75


def test_acceptance_rate_all_green_is_one() -> None:
    assert dashboard.compute_acceptance_rate(5, 0) == 1.0


def test_acceptance_rate_zero_decided_is_none() -> None:
    # No verdicts at all must not raise ZeroDivisionError.
    assert dashboard.compute_acceptance_rate(0, 0) is None


def test_acceptance_rate_ignores_unknown_denominator() -> None:
    # unknown commits are handled by the caller, never in the denominator.
    rate = dashboard.compute_acceptance_rate(2, 2)
    assert rate == 0.5


def test_repometrics_acceptance_rate_property_matches_pure_fn() -> None:
    metrics = dashboard.RepoMetrics(
        repo=dashboard.RepoRef("curso", "equipo-01"),
        commits_total=10,
        gatekeeper_green=4,
        gatekeeper_red=1,
        gatekeeper_unknown=5,
        code_stats=dashboard.CodeStats(None, None, None, "test"),
    )
    assert metrics.acceptance_rate == dashboard.compute_acceptance_rate(4, 1)


def test_repometrics_acceptance_rate_none_when_undecided() -> None:
    metrics = dashboard.RepoMetrics(
        repo=dashboard.RepoRef("curso", "equipo-02"),
        commits_total=3,
        gatekeeper_green=0,
        gatekeeper_red=0,
        gatekeeper_unknown=3,
        code_stats=dashboard.CodeStats(None, None, None, "test"),
    )
    assert metrics.acceptance_rate is None


# --------------------------------------------------------------------------- #
# code-file classification
# --------------------------------------------------------------------------- #
def test_is_code_path_accepts_known_extensions() -> None:
    for name in ("alu.sv", "driver.py", "top.v", "clocks.sdc", "flow.tcl", "ci.yml"):
        assert dashboard.is_code_path(Path("src") / name), name


def test_is_code_path_accepts_extensionless_build_files() -> None:
    assert dashboard.is_code_path(Path("Dockerfile"))
    assert dashboard.is_code_path(Path("Makefile"))
    assert dashboard.is_code_path(Path("sub/dir/Makefile"))


def test_is_code_path_is_case_insensitive_on_extension() -> None:
    assert dashboard.is_code_path(Path("Top.SV"))


def test_is_code_path_rejects_non_code_files() -> None:
    for name in ("README.md", "waveform.vcd", "photo.png", "notes.txt", "data.csv"):
        assert not dashboard.is_code_path(Path(name)), name


def test_is_code_path_rejects_makefile_as_substring() -> None:
    # Only the exact filename counts, not a lookalike.
    assert not dashboard.is_code_path(Path("Makefile.bak"))


# --------------------------------------------------------------------------- #
# gatekeeper reduction (check-runs)
# --------------------------------------------------------------------------- #
def test_reduce_check_runs_none_when_no_match() -> None:
    runs = [{"name": "lint", "status": "completed", "conclusion": "success"}]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") is None


def test_reduce_check_runs_green_on_success() -> None:
    runs = [
        {"name": "gatekeeper / smoke", "status": "completed", "conclusion": "success"},
    ]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "green"


def test_reduce_check_runs_red_on_failure() -> None:
    runs = [
        {"name": "gatekeeper / smoke", "status": "completed", "conclusion": "failure"},
    ]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "red"


def test_reduce_check_runs_green_wins_over_failure() -> None:
    # Any successful gatekeeper run (e.g. a re-run) marks the commit accepted.
    runs = [
        {"name": "gatekeeper", "status": "completed", "conclusion": "failure"},
        {"name": "gatekeeper", "status": "completed", "conclusion": "success"},
    ]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "green"


def test_reduce_check_runs_unknown_when_in_progress() -> None:
    runs = [{"name": "gatekeeper", "status": "in_progress", "conclusion": None}]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "unknown"


def test_reduce_check_runs_skipped_neutral_are_not_red() -> None:
    runs = [
        {"name": "gatekeeper", "status": "completed", "conclusion": "skipped"},
        {"name": "gatekeeper", "status": "completed", "conclusion": "neutral"},
    ]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "unknown"


def test_reduce_check_runs_is_case_insensitive() -> None:
    runs = [{"name": "GateKeeper Pipeline", "status": "completed", "conclusion": "success"}]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "green"


def test_reduce_check_runs_ignores_non_dict_entries() -> None:
    runs = ["oops", None, {"name": "gatekeeper", "status": "completed", "conclusion": "success"}]
    assert dashboard.reduce_check_runs(runs, "gatekeeper") == "green"


# --------------------------------------------------------------------------- #
# gatekeeper reduction (legacy commit statuses)
# --------------------------------------------------------------------------- #
def test_reduce_commit_statuses_green() -> None:
    statuses = [{"context": "ci/gatekeeper", "state": "success"}]
    assert dashboard.reduce_commit_statuses(statuses, "gatekeeper") == "green"


def test_reduce_commit_statuses_red() -> None:
    statuses = [{"context": "ci/gatekeeper", "state": "failure"}]
    assert dashboard.reduce_commit_statuses(statuses, "gatekeeper") == "red"


def test_reduce_commit_statuses_unknown_when_no_match() -> None:
    statuses = [{"context": "ci/other", "state": "success"}]
    assert dashboard.reduce_commit_statuses(statuses, "gatekeeper") == "unknown"


# --------------------------------------------------------------------------- #
# Link-header pagination parsing
# --------------------------------------------------------------------------- #
def test_parse_link_header_none_and_empty() -> None:
    assert dashboard.parse_link_header(None) == {}
    assert dashboard.parse_link_header("") == {}


def test_parse_link_header_next_and_last() -> None:
    header = (
        '<https://api.github.com/repositories/1/commits?page=2>; rel="next", '
        '<https://api.github.com/repositories/1/commits?page=9>; rel="last"'
    )
    links = dashboard.parse_link_header(header)
    assert links["next"] == "https://api.github.com/repositories/1/commits?page=2"
    assert links["last"] == "https://api.github.com/repositories/1/commits?page=9"
    assert "prev" not in links


def test_parse_link_header_ignores_malformed_segments() -> None:
    header = 'garbage, <https://api.github.com/x?page=2>; rel="next"'
    links = dashboard.parse_link_header(header)
    assert links == {"next": "https://api.github.com/x?page=2"}


# --------------------------------------------------------------------------- #
# pagination against a fake API client (no network)
# --------------------------------------------------------------------------- #
class _FakePaginatedApi(dashboard.GitHubApi):
    """A GitHubApi whose HTTP layer is replaced by scripted responses."""

    def __init__(self, responses: list[tuple[object, dict]]) -> None:
        # deliberately skip the real __init__: no token, no network
        self._responses = responses
        self._calls: list[dict] = []

    def get_json_with_headers(self, path, params=None, accept=None, allow_202=False):
        self._calls.append(dict(params or {}))
        index = len(self._calls) - 1
        payload, headers = self._responses[index]
        return payload, headers


def test_iter_paginated_follows_link_next() -> None:
    responses = [
        ([{"sha": "a"}], {"Link": '<https://api.github.com/x?page=2>; rel="next"'}),
        ([{"sha": "b"}], {}),
    ]
    api = _FakePaginatedApi(responses)
    items = api.iter_paginated("/repos/curso/equipo/commits")
    assert [item["sha"] for item in items] == ["a", "b"]
    # second request must ask for page 2
    assert api._calls[1]["page"] == 2


def test_iter_paginated_stops_without_link_on_short_page() -> None:
    responses = [([{"sha": "only"}], {})]
    api = _FakePaginatedApi(responses)
    items = api.iter_paginated("/repos/curso/equipo/commits")
    assert [item["sha"] for item in items] == ["only"]
    assert len(api._calls) == 1


def test_collect_check_runs_follows_link_next() -> None:
    responses = [
        (
            {"total_count": 2, "check_runs": [{"name": "gatekeeper"}]},
            {"Link": '<https://api.github.com/x?page=2>; rel="next"'},
        ),
        ({"total_count": 2, "check_runs": [{"name": "lint"}]}, {}),
    ]
    api = _FakePaginatedApi(responses)
    runs = api.collect_check_runs(dashboard.RepoRef("curso", "equipo"), "deadbeef")
    assert [run["name"] for run in runs] == ["gatekeeper", "lint"]


# --------------------------------------------------------------------------- #
# Markdown rendering contract
# --------------------------------------------------------------------------- #
def _metrics_fixture() -> list["dashboard.RepoMetrics"]:
    return [
        dashboard.RepoMetrics(
            repo=dashboard.RepoRef("curso", "equipo-01"),
            commits_total=8,
            gatekeeper_green=6,
            gatekeeper_red=2,
            gatekeeper_unknown=0,
            code_stats=dashboard.CodeStats(120, 30, 500, "git local: /x"),
        ),
        dashboard.RepoMetrics(
            repo=dashboard.RepoRef("curso", "equipo-02"),
            commits_total=2,
            gatekeeper_green=0,
            gatekeeper_red=0,
            gatekeeper_unknown=2,
            code_stats=dashboard.CodeStats(10, 0, 40, "GitHub stats API"),
        ),
    ]


def test_render_report_has_expected_columns_and_teams() -> None:
    report = dashboard.render_report(
        _metrics_fixture(),
        generated_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        target_label="org:curso",
    )
    # per-team header columns TAs rely on
    for column in (
        "Repo/equipo",
        "Commits totales",
        "Gatekeeper verde",
        "Gatekeeper rojo",
        "Tasa de aceptación",
        "Líneas actuales",
    ):
        assert column in report, column
    # every team appears
    assert "`curso/equipo-01`" in report
    assert "`curso/equipo-02`" in report
    # a TOTAL aggregation row exists
    assert "**TOTAL**" in report


def test_render_report_acceptance_rate_formatting() -> None:
    report = dashboard.render_report(
        _metrics_fixture(),
        generated_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        target_label="org:curso",
    )
    # team-01: 6/8 -> 75.0%
    assert "75.0%" in report
    # team-02 has no verdict -> N/D
    assert "N/D" in report


def test_render_report_undecided_only_reports_nd_rate() -> None:
    undecided = [
        dashboard.RepoMetrics(
            repo=dashboard.RepoRef("curso", "equipo-03"),
            commits_total=1,
            gatekeeper_green=0,
            gatekeeper_red=0,
            gatekeeper_unknown=1,
            code_stats=dashboard.CodeStats(None, None, None, "pending"),
        )
    ]
    report = dashboard.render_report(
        undecided,
        generated_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        target_label="curso/equipo-03",
    )
    assert "N/D" in report
    # target label surfaced for the reader
    assert "curso/equipo-03" in report
