# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Build a static GitHub cohort dashboard without running a webhook service.

The dashboard follows ADR-0007: it polls GitHub on demand or on a schedule and
renders a Markdown report that a professor can inspect or publish as a static
artifact. No persistent server is required.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import cohort

GITHUB_API_ROOT = "https://api.github.com"
DEFAULT_OUTPUT = Path("results/dashboard.md")
CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".json",
    ".mk",
    ".py",
    ".rb",
    ".rs",
    ".sdc",
    ".sh",
    ".sv",
    ".svh",
    ".tcl",
    ".toml",
    ".ts",
    ".v",
    ".vh",
    ".yaml",
    ".yml",
}
CODE_FILENAMES = {"Dockerfile", "Makefile"}


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse an RFC 5988 ``Link`` header into a ``{rel: url}`` mapping.

    GitHub uses this header to advertise pagination (``rel="next"``,
    ``rel="last"``, ...). The parser is defensive: it ignores malformed
    segments instead of raising, so a surprising header never crashes the
    dashboard. A ``None`` or empty header yields an empty mapping.
    """
    if not value:
        return {}
    links: dict[str, str] = {}
    for segment in value.split(","):
        segment = segment.strip()
        if not segment:
            continue
        url_part, _, params_part = segment.partition(";")
        url = url_part.strip().strip("<>").strip()
        if not url or not params_part:
            continue
        rel: str | None = None
        for param in params_part.split(";"):
            key, sep, raw = param.strip().partition("=")
            if sep and key.strip().casefold() == "rel":
                rel = raw.strip().strip('"').strip("'")
                break
        if rel:
            links[rel] = url
    return links


def compute_acceptance_rate(green: int, red: int) -> float | None:
    """Return ``green / (green + red)`` or ``None`` when nothing was decided.

    Commits without a gatekeeper verdict (``unknown``) are deliberately kept
    out of the denominator, and a zero denominator maps to ``None`` (rendered
    as ``N/D``) instead of raising :class:`ZeroDivisionError`.
    """
    decided = green + red
    if decided <= 0:
        return None
    return green / decided


def reduce_check_runs(check_runs: list[Any], gatekeeper_name: str) -> str | None:
    """Reduce Checks-API check-runs to ``green``/``red``/``unknown``.

    Returns ``None`` when no check-run name matches ``gatekeeper_name`` so the
    caller can fall back to the legacy commit-status API. A run counts as
    ``green`` on a ``success`` conclusion, ``red`` on a completed run whose
    conclusion is a genuine failure (not ``skipped``/``neutral``), and
    ``unknown`` while it is still queued or in progress.
    """
    expected = gatekeeper_name.casefold()
    relevant = [
        run
        for run in check_runs
        if isinstance(run, dict)
        and expected in str(run.get("name", "")).casefold()
    ]
    if not relevant:
        return None
    if any(run.get("conclusion") == "success" for run in relevant):
        return "green"
    if any(
        run.get("status") == "completed"
        and run.get("conclusion") not in {"success", "skipped", "neutral"}
        for run in relevant
    ):
        return "red"
    return "unknown"


def reduce_commit_statuses(statuses: list[Any], gatekeeper_name: str) -> str:
    """Reduce legacy commit-status entries to ``green``/``red``/``unknown``."""
    expected = gatekeeper_name.casefold()
    relevant = [
        item
        for item in statuses
        if isinstance(item, dict)
        and expected in str(item.get("context", "")).casefold()
    ]
    if relevant:
        if any(item.get("state") == "success" for item in relevant):
            return "green"
        if any(item.get("state") in {"error", "failure"} for item in relevant):
            return "red"
    return "unknown"


class DashboardError(Exception):
    """Base class for a user-facing dashboard failure."""


class NoRedirect(request.HTTPRedirectHandler):
    """Do not carry authorization or an allowlisted path to another target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class CodeStats:
    added: int | None
    deleted: int | None
    current_lines: int | None
    source: str


@dataclass(frozen=True)
class RepoMetrics:
    repo: RepoRef
    commits_total: int
    gatekeeper_green: int
    gatekeeper_red: int
    gatekeeper_unknown: int
    code_stats: CodeStats

    @property
    def acceptance_rate(self) -> float | None:
        return compute_acceptance_rate(self.gatekeeper_green, self.gatekeeper_red)


class GitHubApi:
    """Small GitHub REST client based on the Python standard library."""

    def __init__(self, token: str, api_root: str = GITHUB_API_ROOT) -> None:
        self._token = token
        self._api_root = api_root.rstrip("/")

    def get_json(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        accept: str = "application/vnd.github+json",
        allow_202: bool = False,
    ) -> Any:
        data, _headers = self.get_json_with_headers(
            path,
            params=params,
            accept=accept,
            allow_202=allow_202,
        )
        return data

    def get_json_with_headers(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        accept: str = "application/vnd.github+json",
        allow_202: bool = False,
    ) -> tuple[Any, Any]:
        code, headers, payload = self._request(
            path,
            params=params,
            accept=accept,
        )
        if code == 202 and allow_202:
            return None, headers
        if code != 200:
            raise DashboardError(
                f"GitHub API returned HTTP {code} for {path}; "
                "verify the token permissions and the repository visibility."
            )
        try:
            return json.loads(payload.decode("utf-8")), headers
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DashboardError(f"GitHub API returned invalid JSON for {path}") from exc

    def iter_paginated(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> list[Any]:
        page = 1
        per_page = 100
        items: list[Any] = []
        while True:
            page_params = dict(params or {})
            page_params["per_page"] = per_page
            page_params["page"] = page
            chunk, headers = self.get_json_with_headers(path, params=page_params)
            if not isinstance(chunk, list):
                raise DashboardError(f"GitHub API returned an unexpected payload for {path}")
            items.extend(chunk)
            link_value = headers.get("Link") if headers is not None else None
            links = parse_link_header(link_value)
            if "next" in links:
                page += 1
                continue
            # Some endpoints omit the Link header on the last (or only) page.
            # When it is absent, fall back to the page-size heuristic so we do
            # not stop early on a full page nor loop forever on a short one.
            if link_value is None and len(chunk) == per_page:
                page += 1
                continue
            return items

    def collect_check_runs(self, repo: "RepoRef", sha: str) -> list[Any]:
        """Return every check-run for a commit, following pagination.

        The check-runs endpoint answers with an object ``{total_count,
        check_runs: [...]}`` and paginates via the Link header, so it cannot
        reuse :meth:`iter_paginated` (which expects a JSON array).
        """
        page = 1
        per_page = 100
        collected: list[Any] = []
        path = f"/repos/{repo.full_name}/commits/{sha}/check-runs"
        while True:
            data, headers = self.get_json_with_headers(
                path,
                params={"per_page": per_page, "page": page},
            )
            runs = data.get("check_runs", []) if isinstance(data, dict) else []
            if not isinstance(runs, list):
                runs = []
            collected.extend(runs)
            total = data.get("total_count") if isinstance(data, dict) else None
            link_value = headers.get("Link") if headers is not None else None
            links = parse_link_header(link_value)
            if "next" in links:
                page += 1
                continue
            if (
                link_value is None
                and runs
                and isinstance(total, int)
                and len(collected) < total
            ):
                page += 1
                continue
            return collected

    def _request(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, Any, bytes]:
        query = f"?{parse.urlencode(params)}" if params else ""
        url = f"{self._api_root}{path}{query}"
        req = request.Request(
            url,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "tfg-dashboard-script",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                return response.getcode(), response.headers, response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._raise_http_error(path, exc, detail)
        except error.URLError as exc:
            raise DashboardError(f"Cannot reach GitHub API: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise DashboardError("GitHub API connection timed out or failed.") from exc
        raise AssertionError("unreachable")

    def _raise_http_error(
        self,
        path: str,
        exc: error.HTTPError,
        detail: str,
    ) -> None:
        headers = exc.headers
        if exc.code == 401:
            raise DashboardError(
                "GITHUB_TOKEN is missing, expired or invalid. "
                "Create a token with access to the target repository or organization."
            ) from exc
        if exc.code == 403:
            remaining = headers.get("X-RateLimit-Remaining")
            reset = headers.get("X-RateLimit-Reset")
            if headers.get("Retry-After") or "secondary rate limit" in detail.lower():
                raise DashboardError(
                    "GitHub API secondary rate limit reached; Retry-After: "
                    f"{headers.get('Retry-After', 'unknown')}."
                ) from exc
            if remaining == "0":
                retry_at = "unknown time"
                if reset and reset.isdigit():
                    retry_at = datetime.fromtimestamp(
                        int(reset), tz=timezone.utc
                    ).isoformat(timespec="seconds")
                raise DashboardError(
                    "GitHub API rate limit reached. "
                    f"Retry after {retry_at}."
                ) from exc
            raise DashboardError(
                f"GitHub denied access to {path}. "
                "Check that the token can read commit checks and repository metadata."
            ) from exc
        if exc.code == 429:
            raise DashboardError(
                "GitHub API rate limit reached; Retry-After: "
                f"{headers.get('Retry-After', 'unknown')}."
            ) from exc
        if exc.code == 404:
            raise DashboardError(
                f"GitHub resource not found for {path}. "
                "Check the owner/name and token visibility."
            ) from exc
        if exc.code == 422:
            raise DashboardError(
                f"GitHub rejected the request for {path} (HTTP 422)."
            ) from exc
        raise DashboardError(
            f"GitHub API request failed for {path} with HTTP {exc.code}."
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repo", help="repositorio objetivo en formato owner/name")
    scope.add_argument("--org", help="organización objetivo")
    parser.add_argument("--config", type=Path, default=os.environ.get("DASHBOARD_CONFIG"),
                        help="JSON de cohorte con allowlist; también DASHBOARD_CONFIG")
    parser.add_argument("--owner", help="limitar la allowlist a una instalación owner")
    parser.add_argument("--validate-config", action="store_true",
                        help="validar sin token ni red")
    parser.add_argument("--print-matrix", action="store_true",
                        help="emitir matriz de instalaciones sin token ni red")
    parser.add_argument("--from-snapshot", type=Path,
                        help="renderizar una foto compatible sin consultar GitHub")
    parser.add_argument("--snapshot-output", type=Path,
                        help="JSON persistente; predeterminado: mismo nombre que --output")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--gatekeeper-name",
        default="Gatekeeper verdict",
        help="nombre exacto del job requerido en modo --repo sin configuración",
    )
    parser.add_argument(
        "--local-repo-path",
        type=Path,
        help="ruta local del repositorio cuando se usa --repo",
    )
    parser.add_argument(
        "--local-repos-root",
        type=Path,
        help="directorio con clones locales de cada repo cuando se usa --org",
    )
    return parser.parse_args()


def require_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise DashboardError(
            "GITHUB_TOKEN is not set. Export it before running the dashboard."
        )
    return token


def parse_repo(full_name: str) -> RepoRef:
    owner, separator, name = full_name.partition("/")
    if not separator or not owner or not name:
        raise DashboardError(
            f"--repo must use owner/name format; received {full_name!r}"
        )
    return RepoRef(owner=owner, name=name)


def list_target_repositories(api: GitHubApi, args: argparse.Namespace) -> list[RepoRef]:
    if args.repo:
        return [parse_repo(args.repo)]

    repos = api.iter_paginated(f"/orgs/{args.org}/repos", params={"type": "all"})
    visible = [
        RepoRef(owner=item["owner"]["login"], name=item["name"])
        for item in repos
        if not item.get("archived", False) and not item.get("disabled", False)
    ]
    if not visible:
        raise DashboardError(
            f"No active repositories were found in organization {args.org!r}."
        )
    return sorted(visible, key=lambda item: item.full_name.lower())


def collect_metrics(
    api: GitHubApi,
    repo: RepoRef,
    gatekeeper_name: str,
    local_path: Path | None,
) -> RepoMetrics:
    commits = api.iter_paginated(f"/repos/{repo.full_name}/commits")
    green = 0
    red = 0
    unknown = 0
    for item in commits:
        sha = item.get("sha")
        if not sha:
            unknown += 1
            continue
        verdict = gatekeeper_verdict(api, repo, sha, gatekeeper_name)
        if verdict == "green":
            green += 1
        elif verdict == "red":
            red += 1
        else:
            unknown += 1

    code_stats = collect_code_stats(api, repo, local_path)
    return RepoMetrics(
        repo=repo,
        commits_total=len(commits),
        gatekeeper_green=green,
        gatekeeper_red=red,
        gatekeeper_unknown=unknown,
        code_stats=code_stats,
    )


def gatekeeper_verdict(
    api: GitHubApi,
    repo: RepoRef,
    sha: str,
    gatekeeper_name: str,
) -> str:
    try:
        check_runs = api.collect_check_runs(repo, sha)
    except DashboardError as exc:
        message = str(exc)
        if not (
            message.startswith("GitHub denied access")
            or message.startswith("GitHub resource not found")
        ):
            raise
        check_runs = []
    verdict = reduce_check_runs(check_runs, gatekeeper_name)
    if verdict is not None:
        return verdict

    status = api.get_json(f"/repos/{repo.full_name}/commits/{sha}/status")
    statuses = status.get("statuses", []) if isinstance(status, dict) else []
    return reduce_commit_statuses(statuses, gatekeeper_name)


def collect_code_stats(
    api: GitHubApi,
    repo: RepoRef,
    local_path: Path | None,
) -> CodeStats:
    if local_path is not None:
        resolved = local_path.resolve()
        if is_git_repository(resolved):
            added, deleted, current_lines = local_code_stats(resolved)
            return CodeStats(
                added=added,
                deleted=deleted,
                current_lines=current_lines,
                source=f"git local: {resolved}",
            )

    frequency = api.get_json(
        f"/repos/{repo.full_name}/stats/code_frequency",
        allow_202=True,
    )
    if frequency is None:
        return CodeStats(
            added=None,
            deleted=None,
            current_lines=None,
            source="GitHub stats pending; retry later or provide a local clone",
        )
    if not isinstance(frequency, list):
        raise DashboardError(
            f"GitHub returned an unexpected stats payload for {repo.full_name}."
        )
    added = sum(int(week[1]) for week in frequency if len(week) >= 3)
    deleted = sum(abs(int(week[2])) for week in frequency if len(week) >= 3)
    return CodeStats(
        added=added,
        deleted=deleted,
        current_lines=None,
        source="GitHub stats API",
    )


def is_git_repository(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def local_code_stats(path: Path) -> tuple[int, int, int]:
    added = 0
    deleted = 0
    proc = subprocess.run(
        ["git", "-C", str(path), "log", "--format=tformat:", "--numstat"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DashboardError(
            f"git log --numstat failed for {path}: {proc.stderr.strip()}"
        )
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_added, raw_deleted, raw_path = parts
        normalized_path = normalize_numstat_path(raw_path)
        if not is_code_path(Path(normalized_path)):
            continue
        if raw_added.isdigit():
            added += int(raw_added)
        if raw_deleted.isdigit():
            deleted += int(raw_deleted)

    current_lines = 0
    tracked_files = git_lines(
        path,
        ["git", "-C", str(path), "ls-files"],
        failure_prefix="git ls-files failed",
    )
    root = git_root(path)
    for relative_path in tracked_files:
        candidate = root / relative_path
        if not is_code_path(candidate):
            continue
        current_lines += count_text_lines(candidate)
    return added, deleted, current_lines


def git_root(path: Path) -> Path:
    return Path(
        git_output(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            failure_prefix="git rev-parse failed",
        ).strip()
    )


def git_lines(path: Path, command: list[str], failure_prefix: str) -> list[str]:
    return [
        line
        for line in git_output(command, failure_prefix=failure_prefix).splitlines()
        if line
    ]


def git_output(command: list[str], failure_prefix: str) -> str:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DashboardError(f"{failure_prefix}: {proc.stderr.strip()}")
    return proc.stdout


def normalize_numstat_path(path: str) -> str:
    if " => " not in path:
        return path
    if "{" not in path or "}" not in path:
        return path.split(" => ", 1)[1]
    prefix, suffix = path.split("{", 1)
    middle, tail = suffix.split("}", 1)
    _, new_name = middle.split(" => ", 1)
    return f"{prefix}{new_name}{tail}"


def is_code_path(path: Path) -> bool:
    if path.name in CODE_FILENAMES:
        return True
    return path.suffix.lower() in CODE_EXTENSIONS


def count_text_lines(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DashboardError(f"Cannot read {path}: {exc}") from exc
    if b"\x00" in data:
        return 0
    if not data:
        return 0
    lines = data.count(b"\n")
    if not data.endswith(b"\n"):
        lines += 1
    return lines


def local_path_for_repo(args: argparse.Namespace, repo: RepoRef) -> Path | None:
    if args.repo:
        return args.local_repo_path
    if args.local_repos_root is None:
        return None
    return args.local_repos_root / repo.name


def render_report(
    metrics: list[RepoMetrics],
    *,
    generated_at: datetime,
    target_label: str,
) -> str:
    total_commits = sum(item.commits_total for item in metrics)
    total_green = sum(item.gatekeeper_green for item in metrics)
    total_red = sum(item.gatekeeper_red for item in metrics)
    total_unknown = sum(item.gatekeeper_unknown for item in metrics)
    added_complete = all(item.code_stats.added is not None for item in metrics)
    deleted_complete = all(item.code_stats.deleted is not None for item in metrics)
    total_added = (
        sum(item.code_stats.added or 0 for item in metrics)
        if added_complete
        else None
    )
    total_deleted = (
        sum(item.code_stats.deleted or 0 for item in metrics)
        if deleted_complete
        else None
    )
    loc_complete = all(item.code_stats.current_lines is not None for item in metrics)
    total_loc = (
        sum(item.code_stats.current_lines or 0 for item in metrics)
        if loc_complete
        else None
    )
    acceptance = compute_acceptance_rate(total_green, total_red)

    lines = [
        "# Tablero estático de la cohorte",
        "",
        f"- **Generado (UTC):** {generated_at.isoformat(timespec='seconds')}",
        f"- **Objetivo consultado:** `{target_label}`",
        "- **Fuente de observabilidad:** GitHub API programada, sin servidor de webhooks.",
        "",
        "## Resumen agregado",
        "",
        f"- **Commits totales:** {total_commits}",
        f"- **Commits aceptados por gatekeeper:** {total_green}",
        f"- **Commits rechazados por gatekeeper:** {total_red}",
        f"- **Commits sin veredicto gatekeeper:** {total_unknown}",
        f"- **Líneas añadidas acumuladas:** {format_int(total_added)}",
        f"- **Líneas eliminadas acumuladas:** {format_int(total_deleted)}",
        f"- **Tasa de aceptación:** {format_rate(acceptance)}",
        f"- **Líneas de código actuales:** {format_int(total_loc)}",
        "",
        "## Equipos / repositorios",
        "",
        "| Repo/equipo | Commits totales | Gatekeeper verde | Gatekeeper rojo | Tasa de aceptación | Líneas actuales | Líneas añadidas | Líneas eliminadas |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in metrics:
        lines.append(
            f"| `{item.repo.full_name}` | {item.commits_total} | "
            f"{item.gatekeeper_green} | {item.gatekeeper_red} | "
            f"{format_rate(item.acceptance_rate)} | "
            f"{format_int(item.code_stats.current_lines)} | "
            f"{format_int(item.code_stats.added)} | "
            f"{format_int(item.code_stats.deleted)} |"
        )
    lines.append(
        f"| **TOTAL** | {total_commits} | {total_green} | {total_red} | "
        f"{format_rate(acceptance)} | {format_int(total_loc)} | "
        f"{format_int(total_added)} | {format_int(total_deleted)} |"
    )

    lines.extend(
        [
            "",
            "## Notas operativas",
            "",
            "- Un commit cuenta como **aceptado** si encuentra un check o status cuyo nombre/contexto contiene `gatekeeper` y terminó en éxito.",
            "- Un commit cuenta como **rechazado** si ese mismo check terminó en fallo, error o cancelación.",
            "- La tasa de aceptación se calcula sobre commits con veredicto (`verde / (verde + rojo)`).",
            "- Las líneas actuales se calculan con `git ls-files` cuando existe un clon local; si no, se reporta `N/D` porque GitHub no expone LOC actual por REST de forma directa.",
            "",
            "## Orquestación sugerida",
            "",
            "- **Manual:** `make dashboard REPO=owner/name` o `make dashboard ORG=curso-org`.",
            "- **Programada:** usar un workflow con `schedule:` y `workflow_dispatch` y artefactos en un repositorio privado del profesorado; nunca Pages público para datos privados.",
            "",
        ]
    )

    source_lines = ["## Procedencia de métricas", ""]
    source_lines.append("| Repo/equipo | Fuente de código |")
    source_lines.append("|---|---|")
    for item in metrics:
        source_lines.append(
            f"| `{item.repo.full_name}` | {item.code_stats.source} |"
        )
    lines.extend(source_lines)
    lines.extend(["", "---", "Reporte informativo; no bloquea el gatekeeper.", ""])
    return "\n".join(lines)


def format_rate(value: float | None) -> str:
    if value is None:
        return "N/D"
    return f"{value * 100:.1f}%"


def format_int(value: int | None) -> str:
    if value is None:
        return "N/D"
    return str(value)


def main() -> int:
    args = parse_args()
    try:
        if args.config:
            config = cohort.load_config(args.config)
        elif args.repo:
            import re

            repo = parse_repo(args.repo)
            config = cohort.validate_config({
                "schema_version": 1, "cohort": repo.full_name,
                "allowed_owners": [repo.owner], "repositories": [repo.full_name],
                "repository_name_regex": "^" + re.escape(repo.name) + "$",
                "ci": {"workflow_file": "gatekeeper.yml", "required_jobs": [args.gatekeeper_name]},
            })
        else:
            raise cohort.CohortError(
                "Indique --config/DASHBOARD_CONFIG o --repo. "
                "--org requiere configuración con allowlist; no se descubren repos automáticamente."
            )
        repos = cohort.selected_repos(config, args.owner or args.org)
        if args.repo:
            repos = [repo for repo in repos if repo.lower() == args.repo.lower()]
            if not repos:
                raise cohort.CohortError("--repo no pertenece a la allowlist seleccionada.")
        if args.print_matrix:
            print(json.dumps(cohort.installation_matrix({**config, "repositories": repos})))
            return 0
        if args.validate_config:
            print(f"Configuración válida: {len(repos)} repositorios autorizados.")
            return 0
        if args.local_repo_path or args.local_repos_root:
            print("Aviso: el colector acotado no usa clones locales ni calcula LOC.", file=sys.stderr)
        if args.from_snapshot:
            snapshot = cohort.read_snapshot(args.from_snapshot, config, repos)
        else:
            snapshot = cohort.Collector(GitHubApi(require_token()), config, DashboardError).collect(repos)
        output = args.output
        snapshot_output = args.snapshot_output or output.with_suffix(".json")
        if output.resolve() == snapshot_output.resolve():
            raise cohort.CohortError("Markdown y snapshot deben tener rutas distintas.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(cohort.render(snapshot, reused=bool(args.from_snapshot)), encoding="utf-8")
        if not args.from_snapshot or args.snapshot_output:
            snapshot_output.parent.mkdir(parents=True, exist_ok=True)
            snapshot_output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    except (DashboardError, cohort.CohortError, OSError) as exc:
        print(f"dashboard error: {exc}", file=sys.stderr)
        return 2

    print(f"static dashboard: {output}")
    return 3 if cohort.incomplete(snapshot) else 0


if __name__ == "__main__":
    raise SystemExit(main())
