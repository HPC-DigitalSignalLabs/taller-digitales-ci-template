# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Bounded, read-only cohort collection and portable static snapshots."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


class CohortError(Exception):
    """Configuration or collection error safe to show to the operator."""


LIMITS = {
    "window_days": (14, 365),
    "max_requests": (500, 5000),
    "max_commits": (50, 1000),
    "max_issues": (100, 1000),
    "max_prs": (50, 1000),
    "max_reviews_per_pr": (100, 1000),
    "max_workflow_runs": (100, 1000),
    "max_jobs_per_run": (100, 1000),
}
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CohortError("No se pudo leer el JSON de configuración.") from exc
    return validate_config(config)


def validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise CohortError("La configuración requiere schema_version: 1.")
    allowed = {"schema_version", "cohort", "allowed_owners", "repositories",
               "repository_name_regex", "collection", "ci"}
    if set(config) - allowed:
        raise CohortError("La configuración contiene campos no reconocidos.")
    owners, repos = config.get("allowed_owners"), config.get("repositories")
    if (not isinstance(owners, list) or not owners or len(owners) > 100
            or any(not isinstance(o, str) or not OWNER.fullmatch(o) for o in owners)
            or len({o.lower() for o in owners}) != len(owners)):
        raise CohortError("allowed_owners debe ser una lista explícita sin duplicados.")
    if (not isinstance(repos, list) or not repos or len(repos) > 500
            or any(not isinstance(r, str) for r in repos)
            or len({r.lower() for r in repos}) != len(repos)):
        raise CohortError("repositories requiere entre 1 y 500 owner/repo únicos.")
    pattern = config.get("repository_name_regex")
    if not isinstance(pattern, str) or len(pattern) > 256:
        raise CohortError("Falta repository_name_regex (máximo 256 caracteres).")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise CohortError("repository_name_regex no es una expresión válida.") from exc
    if not pattern.startswith("^") or not pattern.endswith("$"):
        raise CohortError("La expresión debe estar anclada con ^ y $.")
    for repo in repos:
        owner, sep, name = repo.partition("/")
        if (not sep or owner.lower() not in {o.lower() for o in owners}
                or not REPO.fullmatch(name) or name in {".", ".."}
                or not regex.fullmatch(name)):
            raise CohortError(f"Repositorio fuera de la allowlist o nomenclatura: {repo!r}.")
    label = config.get("cohort")
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise CohortError("cohort requiere una etiqueta de hasta 120 caracteres.")
    collection = config.get("collection", {})
    if not isinstance(collection, dict) or set(collection) - set(LIMITS):
        raise CohortError("collection contiene límites desconocidos.")
    for name, (default, maximum) in LIMITS.items():
        value = collection.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise CohortError(f"{name} debe ser un entero entre 1 y {maximum}.")
    ci = config.get("ci", {})
    if (not isinstance(ci, dict) or set(ci) != {"workflow_file", "required_jobs"}
            or not isinstance(ci.get("workflow_file"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", ci["workflow_file"])):
        raise CohortError("ci requiere workflow_file y required_jobs explícitos.")
    jobs = ci.get("required_jobs")
    if (not isinstance(jobs, list) or not jobs or len(jobs) > 30
            or any(not isinstance(j, str) or not j.strip() or len(j) > 200 for j in jobs)
            or len(set(jobs)) != len(jobs)):
        raise CohortError("required_jobs debe contener nombres exactos, únicos y no vacíos.")
    return config


def selected_repos(config: dict, owner: str | None = None) -> list[str]:
    if owner is not None and owner.lower() not in {
        value.lower() for value in config["allowed_owners"]
    }:
        raise CohortError("--owner no pertenece a allowed_owners.")
    repos = [repo for repo in config["repositories"]
             if owner is None or repo.split("/")[0].lower() == owner.lower()]
    if not repos:
        raise CohortError("No hay repositorios autorizados para este owner.")
    return repos


def installation_matrix(config: dict) -> dict:
    owners = sorted({repo.split("/")[0].lower() for repo in config["repositories"]})
    return {"include": [
        {"owner": owner, "repositories": ",".join(
            repo.split("/")[1] for repo in selected_repos(config, owner)
        )} for owner in owners
    ]}


def policy_hash(config: dict, repos: list[str]) -> str:
    payload = json.dumps({"config": config, "repositories": repos}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def section(source: str, status: str = "completo", items: list | None = None,
            errors: list | None = None) -> dict:
    return {"status": status, "items": items or [], "source": source,
            "observed_at": utc_now(), "errors": errors or []}


class Collector:
    """All remote reads share a hard request budget; no automatic retries."""

    def __init__(self, api: Any, config: dict, api_error: type[Exception]) -> None:
        self.api = api
        self.config = config
        self.api_error = api_error
        self.limits = {key: config.get("collection", {}).get(key, default)
                       for key, (default, _) in LIMITS.items()}
        self.requests = 0
        self.stopped: str | None = None

    def get(self, path: str, params: dict | None = None) -> tuple[Any, Any]:
        if self.stopped:
            raise CohortError(self.stopped)
        if self.requests >= self.limits["max_requests"]:
            raise CohortError("Presupuesto API agotado; datos restantes desconocidos.")
        self.requests += 1
        try:
            data, headers = self.api.get_json_with_headers(path, params=params)
        except self.api_error as exc:
            if "rate limit" in str(exc).lower() or "GITHUB_TOKEN" in str(exc):
                self.stopped = str(exc)
            raise CohortError(str(exc)) from exc
        if headers.get("X-RateLimit-Remaining") == "0":
            self.stopped = (
                "Límite API agotado; reset Unix: "
                + str(headers.get("X-RateLimit-Reset", "desconocido"))
            )
        return data, headers

    def pages(self, path: str, limit: int, *, params: dict | None = None,
              key: str | None = None,
              stop: Callable[[dict], bool] | None = None,
              valid: Callable[[dict], bool] | None = None) -> dict:
        items: list = []
        page = 1
        # Keep per_page fixed so the page offset cannot shift at the cap.
        per_page = min(100, limit)
        try:
            while True:
                data, headers = self.get(
                    path, {**(params or {}), "per_page": per_page, "page": page}
                )
                chunk = data.get(key) if key and isinstance(data, dict) else data
                if not isinstance(chunk, list) or any(not isinstance(i, dict) for i in chunk):
                    raise CohortError("Respuesta API inesperada; no equivale a una lista vacía.")
                for item in chunk:
                    if valid and not valid(item):
                        raise CohortError("Elemento API incompleto o inválido; no se contabiliza como cero.")
                    if stop and stop(item):
                        return section(path, items=items)
                    if len(items) == limit:
                        return section(path, "parcial", items, ["Límite de elementos alcanzado."])
                    items.append(item)
                link = headers.get("Link", "")
                more = 'rel="next"' in link or (not link and len(chunk) == per_page)
                if key and isinstance(data, dict) and isinstance(data.get("total_count"), int):
                    more = 'rel="next"' in link or len(items) < data["total_count"]
                if not more:
                    return section(path, items=items)
                if len(items) >= limit:
                    return section(path, "parcial", items, ["Límite de elementos alcanzado."])
                if not chunk:
                    raise CohortError("Paginación inconsistente: página vacía con continuación.")
                page += 1
        except CohortError as exc:
            return section(path, "parcial" if items else "error", items, [str(exc)])

    def collect(self, repos: list[str], now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=self.limits["window_days"])).isoformat(timespec="seconds")
        until = now.isoformat(timespec="seconds")
        results = [self.repository(repo, since, until) for repo in repos]
        return {
            "schema_version": 1, "cohort": self.config["cohort"],
            "policy_hash": policy_hash(self.config, repos), "generated_at": utc_now(),
            "window": {"since": since, "until": until},
            "api_requests": self.requests, "request_budget": self.limits["max_requests"],
            "repositories": results,
        }

    def repository(self, repo: str, since: str, until: str) -> dict:
        base = f"/repos/{repo}"
        result: dict[str, Any] = {"repository": repo, "default_branch": None}
        try:
            metadata, _ = self.get(base)
            if (not isinstance(metadata, dict)
                    or str(metadata.get("full_name", "")).lower() != repo.lower()
                    or not isinstance(metadata.get("default_branch"), str)):
                raise CohortError("Identidad o rama del repositorio inesperada.")
            result["default_branch"] = metadata["default_branch"]
        except CohortError as exc:
            for key in ("commits", "issues", "pull_requests", "reviews", "ci"):
                result[key] = section(base, "error", errors=[str(exc)])
            return result
        commits = self.pages(base + "/commits", self.limits["max_commits"],
                             params={"sha": result["default_branch"], "since": since, "until": until},
                             valid=lambda item: isinstance(item.get("sha"), str)
                             and bool(re.fullmatch(r"[0-9a-fA-F]{40,64}", item["sha"]))
                             and isinstance(item.get("commit"), dict)
                             and (item["commit"].get("committer") is None
                                  or isinstance(item["commit"]["committer"], dict)))
        commits["items"] = [{
            "sha": item.get("sha"), "author": login(item.get("author")),
            "date": (item["commit"].get("committer") or {}).get("date"),
        } for item in commits["items"]]
        result["commits"] = commits
        issues = self.pages(base + "/issues", self.limits["max_issues"],
                            params={"state": "all", "since": since,
                                    "sort": "updated", "direction": "desc"},
                            valid=valid_activity)
        issues["items"] = [activity(item) for item in issues["items"]
                           if "pull_request" not in item and in_window(item.get("updated_at"), since, until)]
        issues["scope"] = "Issues actualizadas en la ventana; el límite incluye PR devueltas por /issues."
        result["issues"] = issues
        prs = self.pages(
            base + "/pulls", self.limits["max_prs"],
            params={"state": "all", "sort": "updated", "direction": "desc"},
            stop=lambda item: older(item.get("updated_at"), since),
            valid=valid_activity,
        )
        prs["items"] = [activity(item) for item in prs["items"]
                        if in_window(item.get("updated_at"), since, until)]
        result["pull_requests"] = prs
        reviews = section(base + "/pulls/{number}/reviews")
        failed_reviews = 0
        for pr in prs["items"]:
            part = self.pages(base + f"/pulls/{pr['number']}/reviews",
                              self.limits["max_reviews_per_pr"],
                              valid=lambda item: type(item.get("id")) is int
                              and isinstance(item.get("state"), str)
                              and (item["state"] == "PENDING" or timestamp(item.get("submitted_at")) is not None))
            reviews["items"].extend({
                "pr": pr["number"], "id": item.get("id"),
                "author": login(item.get("user")), "state": item.get("state"),
                "submitted_at": item.get("submitted_at"),
            } for item in part["items"] if in_window(item.get("submitted_at"), since, until))
            if part["status"] != "completo":
                reviews["status"] = "parcial"
                failed_reviews += 1
                reviews["errors"].extend(part["errors"])
        if failed_reviews and failed_reviews == len(prs["items"]) and not reviews["items"]:
            reviews["status"] = "error"
        if prs["status"] != "completo":
            reviews["status"] = "parcial" if prs["items"] else "desconocido"
            reviews["errors"].append("La lista de PR no está completa.")
        reviews["observed_at"] = utc_now()
        reviews["scope"] = "Revisiones enviadas en la ventana para las PR observadas; no comentarios ni borradores."
        result["reviews"] = reviews
        result["ci"] = self.ci(repo, result["default_branch"], commits, since, until)
        return result

    def ci(self, repo: str, branch: str, commits: dict, since: str, until: str) -> dict:
        ci = self.config["ci"]
        base = f"/repos/{repo}/actions"
        path = base + "/workflows/" + ci["workflow_file"]
        try:
            workflow, _ = self.get(path)
            if (not isinstance(workflow, dict) or type(workflow.get("id")) is not int
                    or workflow.get("path") != ".github/workflows/" + ci["workflow_file"]):
                raise CohortError("No coincide la identidad exacta del workflow.")
        except CohortError as exc:
            return section(path, "error", errors=[str(exc)])
        runs = self.pages(
            base + f"/workflows/{workflow['id']}/runs", self.limits["max_workflow_runs"],
            key="workflow_runs",
            params={"branch": branch, "event": "push", "created": f"{since}..{until}"},
        )
        result = section(runs["source"], runs["status"], errors=list(runs["errors"]))
        result["identity"] = {**ci, "workflow_id": workflow["id"], "branch": branch, "event": "push"}
        latest: dict[str, dict] = {}
        for run in runs["items"]:
            if (run.get("workflow_id") != workflow["id"] or run.get("event") != "push"
                    or run.get("head_branch") != branch
                    or str((run.get("head_repository") or {}).get("full_name", "")).lower() != repo.lower()):
                result["status"] = "parcial"
                result["errors"].append("Se ignoró una ejecución cuya identidad no coincide.")
                continue
            sha = run.get("head_sha")
            if (not isinstance(sha, str) or not isinstance(run.get("status"), str)
                    or any(type(run.get(k)) is not int for k in ("id", "run_number", "run_attempt"))):
                result["status"] = "parcial"
                result["errors"].append("Ejecución con identificadores incompletos.")
                continue
            rank = lambda value: (value["run_number"], value["run_attempt"], value["id"])
            if sha not in latest or rank(run) > rank(latest[sha]):
                latest[sha] = run
        for commit in commits["items"]:
            sha = commit["sha"]
            run = latest.get(sha)
            entry = {"sha": sha, "verdict": "desconocido", "run_id": None,
                     "observed_at": utc_now()}
            if not run:
                entry["verdict"] = "sin_ejecuciones" if result["status"] == "completo" else "desconocido"
            elif result["status"] != "completo":
                # An unseen page can contain a newer re-run; never use a stale green.
                entry["reason"] = "Listado de ejecuciones incompleto."
            else:
                entry.update({"run_id": run["id"], "attempt": run["run_attempt"],
                              "updated_at": run.get("updated_at")})
                jobs = self.pages(
                    base + f"/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",
                    self.limits["max_jobs_per_run"], key="jobs",
                )
                entry["source"] = jobs["source"]
                entry["verdict"] = jobs_verdict(jobs, ci["required_jobs"], sha, run["status"])
                if jobs["status"] != "completo":
                    result["errors"].extend(jobs["errors"])
                entry["jobs_status"] = jobs["status"]
            result["items"].append(entry)
        if commits["status"] != "completo":
            result["status"] = "parcial" if commits["items"] else "desconocido"
            result["errors"].append("Muestra de commits incompleta.")
        if any(item.get("jobs_status") != "completo" for item in result["items"] if "jobs_status" in item):
            result["status"] = "parcial"
        result["observed_at"] = utc_now()
        return result


def login(user: Any) -> str:
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        return user["login"]
    return "sin atribución"


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def in_window(value: Any, since: str, until: str) -> bool:
    date = timestamp(value)
    return date is not None and timestamp(since) <= date <= timestamp(until)


def older(value: Any, since: str) -> bool:
    date = timestamp(value)
    return date is not None and date < timestamp(since)


def activity(item: dict) -> dict:
    return {**{key: item.get(key) for key in ("number", "state", "created_at", "updated_at", "closed_at")},
            "author": login(item.get("user")), "draft": item.get("draft", False),
            "merged_at": item.get("merged_at")}


def valid_activity(item: dict) -> bool:
    return (type(item.get("number")) is int and item["number"] > 0
            and item.get("state") in {"open", "closed"}
            and timestamp(item.get("created_at")) is not None
            and timestamp(item.get("updated_at")) is not None)


def jobs_verdict(jobs: dict, required: list[str], sha: str, run_status: str) -> str:
    if jobs["status"] != "completo":
        return "desconocido"
    selected = [job for job in jobs["items"] if job.get("name") in required]
    if any(sum(job.get("name") == name for job in selected) != 1 for name in required):
        return "desconocido"
    if any(job.get("head_sha") != sha for job in selected):
        return "desconocido"
    if run_status != "completed" or any(job.get("status") != "completed" for job in selected):
        return "pendiente"
    conclusions = {job.get("conclusion") for job in selected}
    if conclusions & {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}:
        return "rojo"
    return "verde" if conclusions == {"success"} else "desconocido"


def read_snapshot(path: Path, config: dict, repos: list[str]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data["schema_version"] != 1 or data["policy_hash"] != policy_hash(config, repos)
                or [r["repository"] for r in data["repositories"]] != repos
                or not isinstance(data["cohort"], str)
                or type(data["api_requests"]) is not int
                or type(data["request_budget"]) is not int
                or timestamp(data["generated_at"]) is None
                or timestamp(data["window"]["since"]) is None
                or timestamp(data["window"]["until"]) is None):
            raise ValueError("snapshot incompatible")
        if data.get("provenance", "github-rest") not in {"github-rest", "synthetic-fixture"}:
            raise ValueError("procedencia incompatible")
        for repo in data["repositories"]:
            if repo["default_branch"] is not None and not isinstance(repo["default_branch"], str):
                raise ValueError("rama incompatible")
            for key in ("commits", "issues", "pull_requests", "reviews", "ci"):
                metric = repo[key]
                if (metric["status"] not in {"completo", "parcial", "error", "desconocido"}
                        or not isinstance(metric["items"], list)
                        or not isinstance(metric["errors"], list)
                        or any(not isinstance(message, str) for message in metric["errors"])
                        or not isinstance(metric["source"], str)
                        or timestamp(metric["observed_at"]) is None):
                    raise ValueError("sección incompatible")
                for item in metric["items"]:
                    if not isinstance(item, dict):
                        raise ValueError("elemento incompatible")
                    if key != "ci" and not isinstance(item["author"], str):
                        raise ValueError("autor incompatible")
                    if key in {"issues", "pull_requests"} and not valid_activity(item):
                        raise ValueError("actividad incompatible")
                    if key in {"commits", "ci"} and not isinstance(item["sha"], str):
                        raise ValueError("SHA incompatible")
                    if key == "reviews" and (
                        type(item["pr"]) is not int or not isinstance(item["state"], str)
                        or timestamp(item["submitted_at"]) is None
                    ):
                        raise ValueError("revisión incompatible")
                    if key == "ci" and (
                        item["verdict"] not in {"verde", "rojo", "pendiente", "desconocido", "sin_ejecuciones"}
                        or (item["run_id"] is not None and type(item["run_id"]) is not int)
                    ):
                        raise ValueError("CI incompatible")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CohortError("Snapshot inválido o de otra configuración/allowlist.") from exc
    return data


def cell(value: Any) -> str:
    return html.escape(str(value)).replace("|", "&#124;").replace("`", "&#96;").replace("\r", " ").replace("\n", " ")


def count_label(metric: dict) -> str:
    if metric["status"] == "completo":
        return str(len(metric["items"]))
    if metric["items"]:
        return f">={len(metric['items'])} (parcial)"
    return "N/D"


def render(snapshot: dict, *, reused: bool = False) -> str:
    synthetic = snapshot.get("provenance") == "synthetic-fixture"
    lines = [
        "# Tablero estático de la cohorte", "",
        *(["> DEMO NO LIVE — datos sintéticos; no se consultó GitHub. "
           "No demuestra autorización de una App ni actividad real.", ""] if synthetic else []),
        f"- Cohorte: {cell(snapshot['cohort'])}",
        f"- Foto original (UTC): {cell(snapshot['generated_at'])}",
        f"- Ventana: {cell(snapshot['window']['since'])} a {cell(snapshot['window']['until'])}",
        (f"- Procedencia: fixture sintético, {snapshot['api_requests']} consultas reales."
         if synthetic else
         f"- Procedencia: REST GitHub, {snapshot['api_requests']}/{snapshot['request_budget']} consultas presupuestadas."),
        f"- Modo: {'snapshot reutilizado, sin consultas ni actualización' if reused else 'recolección periódica acotada'}.",
        "- Acceso restringido: no publicar datos privados en Pages público.",
        "- Las cifras parciales son mínimos observados; N/D no equivale a cero.", "",
        "| Repo/equipo | Commits observados | Issues actualizadas | PR actualizadas | Revisiones enviadas | CI verde / rojo / sin ejecuciones / pendiente / desconocido | Aceptación observada |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    totals = Counter()
    for repo in snapshot["repositories"]:
        counts = Counter(item["verdict"] for item in repo["ci"]["items"])
        totals.update(counts)
        decided = counts["verde"] + counts["rojo"]
        rate = f"{100 * counts['verde'] / decided:.1f}%" if decided else "N/D"
        ci = " / ".join(str(counts[key]) for key in
                        ("verde", "rojo", "sin_ejecuciones", "pendiente", "desconocido"))
        if repo["ci"]["status"] != "completo":
            ci += f" ({repo['ci']['status']})"
        if not repo["ci"]["items"] and repo["ci"]["status"] != "completo":
            ci = "N/D"
        lines.append("| " + " | ".join([
            cell(repo["repository"]), count_label(repo["commits"]), count_label(repo["issues"]),
            count_label(repo["pull_requests"]), count_label(repo["reviews"]), ci, rate,
        ]) + " |")
    ci_available = any(repo["ci"]["items"] or repo["ci"]["status"] == "completo"
                       for repo in snapshot["repositories"])
    aggregate = (f"{totals['verde']} verdes, {totals['rojo']} rojos."
                 if ci_available else "N/D, no hay datos CI disponibles.")
    lines.extend([
        "", f"Veredictos observados en la cohorte: {aggregate}",
        "No son totales históricos ni certifican cobertura completa. La aceptación solo usa verdes y rojos.",
        "LOC actual e histórico: N/D en este modo; no se barren clones ni todo el historial.", "",
        "## Evidencia y procedencia por repositorio", "",
    ])
    for repo in snapshot["repositories"]:
        lines.extend([f"### {cell(repo['repository'])}", "",
                      f"Rama por defecto: {cell(repo['default_branch'])}", "",
                      "| Métrica | Estado | Observada (UTC) | Endpoint |",
                      "|---|---|---|---|"])
        notes = []
        for key in ("commits", "issues", "pull_requests", "reviews", "ci"):
            metric = repo[key]
            lines.append(f"| {key} | {metric['status']} | {cell(metric['observed_at'])} | {cell(metric['source'])} |")
            if metric.get("scope"):
                notes.append(f"- {cell(metric['scope'])}")
            for message in dict.fromkeys(metric["errors"]):
                notes.append(f"- Aviso ({key}): {cell(message)}")
        lines.extend(["", *notes])
        lines.extend(["", "### Actividad contextualizada", "",
                      "Solo muestra autores atribuidos por GitHub, no esfuerzo, calidad ni nota. "
                      "Incluye bots, merges, squash y trabajo compartido sin corregirlos.", "",
                      "| Cuenta | Commits | Issues creadas | PR creadas | Revisiones enviadas |",
                      "|---|---:|---:|---:|---:|"])
        people: dict[str, Counter] = defaultdict(Counter)
        for key in ("commits", "issues", "pull_requests", "reviews"):
            metric = repo[key]
            for item in metric["items"]:
                if key in {"issues", "pull_requests"} and not in_window(
                    item["created_at"], snapshot["window"]["since"], snapshot["window"]["until"]
                ):
                    continue
                people[item["author"]][key] += 1
        for person in sorted(people):
            values = []
            for key in ("commits", "issues", "pull_requests", "reviews"):
                count = people[person][key]
                values.append(str(count) if repo[key]["status"] == "completo"
                              else (f">={count}" if count else "N/D"))
            lines.append("| " + cell(person) + " | " + " | ".join(values) + " |")
        lines.extend(["", "Issues/PR: número y estado actuales de los elementos observados. "
                      "Revisiones: estado del evento, no aprobación efectiva actual.", ""])
        for key in ("issues", "pull_requests"):
            if repo[key]["items"]:
                details = ", ".join(f"#{item['number']} {cell(item['state'])}"
                                    + (" (merged)" if item.get("merged_at") else "")
                                    for item in repo[key]["items"])
                lines.append(f"- {key}: {details}")
        for review in repo["reviews"]["items"]:
            lines.append(f"- Revisión PR #{review['pr']}: {cell(review['author'])}, "
                         f"{cell(review['state'])}, {cell(review['submitted_at'])}.")
        lines.extend(["", "| SHA | Veredicto | Run / intento | Actualización GitHub |",
                      "|---|---|---|---|"])
        for item in repo["ci"]["items"]:
            lines.append(f"| {cell(item['sha'])} | {cell(item['verdict'])} | "
                         f"{cell(item['run_id'])} / {cell(item.get('attempt', 'N/D'))} | "
                         f"{cell(item.get('updated_at', 'N/D'))} |")
    lines.extend(["", "La API no es una transacción: pueden cambiar datos durante la consulta. "
                  "CI solo considera push en la rama por defecto, workflow exacto, último run/intento "
                  "observado por SHA y todos los jobs requeridos. Sin ejecuciones significa "
                  "sin coincidencias en esta ventana, no ausencia histórica. No verifica que "
                  "el contenido del workflow sea inmutable; protegerlo es responsabilidad docente.", ""])
    return "\n".join(lines)


def incomplete(snapshot: dict) -> bool:
    return any(repo[key]["status"] != "completo"
               for repo in snapshot["repositories"]
               for key in ("commits", "issues", "pull_requests", "reviews", "ci"))
