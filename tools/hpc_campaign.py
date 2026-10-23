#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Campañas SLURM acotadas. Controlador stdlib compatible con Python 3.9+.

plan crea una instantánea local; submit no envía sin --execute.
No implementa SSH, transferencias, builds de imágenes ni escalamiento automático.
La verificación continúa usando el Python 3.10+ fijado en la imagen.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT_FILES = {"Makefile", "sim.mk", "pytest.ini", "requirements.txt", "requirements-dev.txt"}
ROOT_DIRS = {"src", "lib", "tests", "tools", "hpc"}
EXCLUDE = {".git", "__pycache__", ".pytest_cache", ".venv", "results", "logs",
           "sim_build", "output_files", "obj_dir", ".mypy_cache", ".ruff_cache"}
FIELDS = ["JobID", "State", "ExitCode", "Submit", "Start", "End", "ElapsedRaw",
          "AllocCPUS", "TotalCPU", "MaxRSS", "ReqMem"]
ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED",
          "REQUEUED", "RESIZING"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    """Reemplazo atómico en el mismo filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def template_root(path: Path) -> Path:
    path = path.resolve()
    if (path / "template" / "sim.mk").is_file():
        path /= "template"
    if not (path / "Makefile").is_file() or not (path / "sim.mk").is_file():
        raise ValueError("source debe ser la raíz del repositorio o de template.")
    return path


def inventory(root: Path) -> dict[str, str]:
    """Lista permitida; nunca copia todo el repo ni recorre las salidas."""
    found: dict[str, str] = {}
    size = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        relative = Path(base).relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE and
                         not d.startswith(".") and not d.startswith("sim_build") and
                         (relative.parts or d in ROOT_DIRS))
        for name in dirs:
            if (Path(base) / name).is_symlink():
                raise ValueError(f"No se admiten symlinks de fuente: {Path(base) / name}")
        for name in sorted(files):
            if not relative.parts and name not in ROOT_FILES:
                continue
            if name.endswith((".pyc", ".vcd", ".fst", ".sif")):
                continue
            item = Path(base) / name
            if item.is_symlink():
                raise ValueError(f"No se admiten symlinks de fuente: {item}")
            size += item.stat().st_size
            if size > 64 * 1024 * 1024 or len(found) >= 10000:
                raise ValueError("Instantánea excede 64 MiB o 10000 archivos; revisar fuentes.")
            found[item.relative_to(root).as_posix()] = sha256(item)
    return dict(sorted(found.items()))


def tree_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def positive(value: object, name: str, ceiling: int) -> int:
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ValueError(f"{name} debe estar entre 1 y {ceiling}.")
    return value


def validate_config(config: dict) -> dict:
    c = dict(config)
    for key, ceiling in (("max_tasks", 256), ("max_concurrency", 32),
                         ("max_cpus", 32), ("max_memory_mb", 131072),
                         ("max_time_minutes", 120)):
        positive(c.get(key), key, ceiling)
    for key, bound in (("cpus", "max_cpus"), ("memory_mb", "max_memory_mb"),
                       ("time_minutes", "max_time_minutes")):
        positive(c.get(key), key, c[bound])
    for key in ("partition", "account", "qos", "module", "runtime"):
        value = c.get(key, "")
        if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9_./-]+", value)):
            raise ValueError(f"Valor inválido: {key}")
        c[key] = value
    if not c["partition"] or c["runtime"] not in {"apptainer", "singularity"}:
        raise ValueError("Se requiere partition y runtime apptainer o singularity.")
    if c["partition"].startswith("nukwa"):
        raise ValueError("Las particiones GPU nukwa están fuera de esta campaña CPU.")
    for key in ("image", "results_root"):
        value = c.get(key)
        if not isinstance(value, str) or not value.startswith("/") or any(x in value for x in ",:\n\r"):
            raise ValueError(f"{key} requiere ruta Linux absoluta sin coma, dos puntos ni saltos.")
    c.setdefault("module_init", "")
    if not isinstance(c["module_init"], str):
        raise ValueError("module_init debe ser texto, vacío si no se necesita.")
    c.setdefault("image_sha256", None)
    if c["image_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", c["image_sha256"]):
        raise ValueError("image_sha256 debe ser SHA256 hexadecimal o null.")
    return c


def validate_manifest(m: dict) -> dict:
    if m.get("schema") != 1:
        raise ValueError("Versión de manifiesto no soportada.")
    c = validate_config(m["config"])
    total = positive(m["batch_size"], "batch_size", c["max_tasks"]) * positive(
        m["repetitions"], "repetitions", c["max_tasks"])
    if total > c["max_tasks"] or total != len(m["tasks"]):
        raise ValueError("El total de tareas supera el límite o no coincide con tasks.")
    positive(m["concurrency"], "concurrency", min(c["max_concurrency"], total))
    if m["mode"] not in {"regression", "demo"}:
        raise ValueError("Modo inválido.")
    target = m["pytest_target"]
    if not re.fullmatch(r"tests/[A-Za-z0-9_/]+(?:\.py)?", target) or ".." in target:
        raise ValueError("pytest_target debe ser una ruta relativa dentro de tests.")
    positive(m["cycles"], "cycles", 1000000)
    for index, task in enumerate(m["tasks"]):
        if task["index"] != index or type(task["seed"]) is not int or not 0 <= task["seed"] < 2**32:
            raise ValueError("Índice o semilla inválidos.")
    return c


def plan(args: argparse.Namespace) -> int:
    c = validate_config(load(args.config))
    source = template_root(args.source)
    output = args.output.resolve()
    # Evita que una campaña se capture a sí misma en una siguiente instantánea.
    if output == source or any(output.is_relative_to(source / d) for d in ROOT_DIRS):
        raise ValueError("output debe estar fuera de los directorios de fuentes.")
    total = args.batch_size * args.repetitions
    if total < 1 or total > c["max_tasks"]:
        raise ValueError("batch-size * repetitions debe estar dentro de max_tasks.")
    tasks = [{"index": i, "repetition": i // args.batch_size,
              "sample": i % args.batch_size,
              "seed": args.seed + (i % args.batch_size if args.seed_mode == "indexed" else 0)}
             for i in range(total)]
    git_sha = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                             text=True, capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                           text=True, capture_output=True, check=True).stdout
    files = inventory(source)
    m = {"schema": 1, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
         "source": {"git_sha": git_sha, "dirty": bool(dirty), "dirty_status": dirty,
                    "original_path": str(source), "files": files, "digest": tree_digest(files)},
         "config": c, "mode": args.mode, "batch_size": args.batch_size,
         "repetitions": args.repetitions, "concurrency": args.concurrency,
         "seed_mode": args.seed_mode, "cycles": args.cycles, "tasks": tasks,
         "pytest_target": args.pytest_target, "cache_policy": "fresh-build-per-attempt",
         "population": "synthetic-replicas-not-students"}
    validate_manifest(m)
    if not (source / args.pytest_target).exists():
        raise ValueError("pytest_target no existe en source.")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    snapshot = output / "source"
    for name in files:
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    if inventory(snapshot) != files:
        raise ValueError("Las fuentes cambiaron durante la captura; crear otro plan.")
    # Windows puede entregar CRLF incluso con .gitattributes. El manifiesto
    # distingue bytes de origen de bytes ejecutables normalizados a LF.
    for name in files:
        if Path(name).suffix in {".sh", ".slurm", ".mk"} or Path(name).name == "Makefile":
            target = snapshot / name
            target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
    m["source"]["original_digest"] = m["source"]["digest"]
    m["source"]["files"] = inventory(snapshot)
    m["source"]["digest"] = tree_digest(m["source"]["files"])
    m["source"]["normalization"] = "LF en .sh, .slurm, .mk y Makefile"
    write_json(output / "manifest.json", m)
    print(json.dumps({"manifest": str(output / "manifest.json"), "tasks": total,
                      "concurrency": args.concurrency, "source_digest": m["source"]["digest"],
                      "peak_cpus": args.concurrency * c["cpus"],
                      "peak_memory_mb": args.concurrency * c["memory_mb"],
                      "max_cpu_minutes": total * c["cpus"] * c["time_minutes"],
                      "submitted": False}, indent=2))
    return 0


def verify_snapshot(manifest: Path, m: dict) -> Path:
    source = manifest.parent / "source"
    if inventory(source) != m["source"]["files"] or tree_digest(m["source"]["files"]) != m["source"]["digest"]:
        raise ValueError("Instantánea modificada; no se puede atribuir al manifiesto.")
    return source


def submission_command(manifest: Path, m: dict) -> list[str]:
    c = validate_manifest(m)
    source = manifest.parent / "source"
    script = "regression.slurm" if m["mode"] == "regression" else "demo-showcase.slurm"
    command = ["sbatch", "--parsable", "--no-requeue", "--export=NONE", "--open-mode=append",
               "--job-name=hpc-campaign",
               "--nodes=1", "--ntasks=1", f"--partition={c['partition']}",
               f"--cpus-per-task={c['cpus']}", f"--mem={c['memory_mb']}M",
               f"--time={c['time_minutes']}", f"--array=0-{len(m['tasks']) - 1}%{m['concurrency']}",
               f"--chdir={source}", f"--output={manifest.parent / 'logs' / '%A_%a.out'}",
               f"--error={manifest.parent / 'logs' / '%A_%a.err'}"]
    for key in ("account", "qos"):
        if c[key]:
            command.append(f"--{key}={c[key]}")
    return command + [str(source / "hpc" / script), str(source / "tools" / "hpc_campaign.py"), str(manifest)]


def submit(args: argparse.Namespace) -> int:
    path = args.manifest.resolve()
    m = load(path)
    verify_snapshot(path, m)
    command = submission_command(path, m)
    print(json.dumps({"argv": command, "execute": args.execute}, indent=2))
    if not args.execute:
        return 0
    if os.name != "posix":
        raise ValueError("submit --execute se ejecuta en el host Linux con SLURM.")
    if not Path(m["config"]["image"]).is_file():
        raise ValueError("No existe la imagen configurada.")
    if m["source"]["dirty"] and not args.allow_dirty:
        raise ValueError("Fuente dirty: revisar y autorizar con --allow-dirty.")
    # La reclamación exclusiva permanece incluso ante timeout/error: nunca reenvía
    # automáticamente cuando sbatch pudo aceptar el trabajo y perderse la respuesta.
    claim = path.parent / "submission-claim.json"
    with claim.open("x", encoding="utf-8") as handle:
        json.dump({"manifest_sha256": sha256(path), "status": "submission-attempted"}, handle)
    (path.parent / "logs").mkdir(exist_ok=True, mode=0o700)
    shared_root = Path(m["config"]["results_root"])
    shared_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = shared_root / ".submission.lock"
    # Una raíz compartida por usuario evita carreras entre campañas concurrentes.
    # Tras una caída se inspecciona el lock manualmente, nunca se rompe por edad.
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(path))
    try:
        require_idle = getattr(args, "require_idle", False)
        queue_command = ["squeue", "--noheader", "--array", "--user", str(os.getuid()),
                         "--format=%i"]
        if not require_idle:
            queue_command += ["--name", "hpc-campaign"]
        queue = subprocess.run(queue_command,
                               capture_output=True, text=True, timeout=30, check=True)
        active = len([line for line in queue.stdout.splitlines() if line.strip()])
        if require_idle and active:
            raise ValueError("El piloto requiere cola del usuario vacía; no se envió ningún job.")
        if active + len(m["tasks"]) > m["config"]["max_tasks"]:
            raise ValueError("Jobs activos + nuevo lote exceden max_tasks; esperar a recolectar.")
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        match = re.fullmatch(r"([0-9]+)(?:;([A-Za-z0-9_.-]+))?", proc.stdout.strip())
        receipt = {"argv": command, "stdout": proc.stdout, "stderr": proc.stderr,
                   "returncode": proc.returncode, "manifest_sha256": sha256(path)}
        if proc.returncode == 0 and match:
            receipt.update(job_id=match[1], cluster=match[2])
        write_json(path.parent / "submission.json", receipt)
        if "job_id" not in receipt:
            raise ValueError("Envío no confirmado. Revisar SLURM; NO volver a enviar este plan.")
        print(f"Job array confirmado: {receipt['job_id']}")
    except subprocess.TimeoutExpired as exc:
        if exc.cmd and exc.cmd[0] == "squeue":
            raise ValueError("Timeout consultando la cola; no se invocó sbatch. Revisar el plan y la reclamación.") from exc
        write_json(path.parent / "submission.json", {"error": "timeout; aceptación desconocida",
                   "manifest_sha256": sha256(path)})
        raise ValueError("Timeout de sbatch. Consultar squeue; no reintentar a ciegas.") from exc
    finally:
        lock.unlink()
    return 0


def junit_valid(path: Path) -> bool:
    """No hay verde con archivo ausente, cero casos, errores o skips."""
    try:
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
        if root.tag not in {"testsuite", "testsuites"} or not cases:
            return False
        for suite in (node for node in root.iter() if node.tag in {"testsuite", "testsuites"}):
            if any(int(suite.get(attr, "0")) for attr in ("failures", "errors", "skipped")):
                return False
        return not any(list(case.iter(tag)) for case in cases for tag in ("failure", "error", "skipped"))
    except (OSError, ET.ParseError, ValueError):
        return False


def new_attempt(root: Path, job: str, task: int, restart: str) -> Path:
    if not re.fullmatch(r"\d+", job) or not re.fullmatch(r"\d+", restart):
        raise ValueError("Identidad SLURM inválida.")
    parent = root / job / str(task)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix=f"attempt-{restart}-", dir=parent))


def clean_host_env() -> dict[str, str]:
    """El runtime puede necesitar LD_LIBRARY_PATH de su módulo, no overrides del usuario."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("APPTAINER", "SINGULARITY")) and
            k not in {"PYTHONPATH", "PYTHONHOME", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS",
                      "BASH_ENV", "ENV", "PYTEST_ADDOPTS"}}


def run_task(args: argparse.Namespace) -> int:
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("run-task requiere SLURM; no ejecutar cargas en login.")
    manifest = args.manifest.resolve()
    m = load(manifest)
    c = validate_manifest(m)
    if args.mode != m["mode"]:
        raise ValueError("Modo del script y manifiesto no coinciden.")
    task_id = int(os.environ.get("SLURM_ARRAY_TASK_ID", "0"))
    if not 0 <= task_id < len(m["tasks"]):
        raise ValueError("Tarea fuera del manifiesto.")
    job = os.environ.get("SLURM_ARRAY_JOB_ID", os.environ["SLURM_JOB_ID"])
    task = m["tasks"][task_id]
    attempt = new_attempt(Path(c["results_root"]), job, task_id,
                          os.environ.get("SLURM_RESTART_COUNT", "0"))
    record = {"job_id": job, "task": task_id, "seed": task["seed"],
              "manifest_sha256": sha256(manifest), "source_digest": m["source"]["digest"],
              "attempt": str(attempt), "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "status": "FAILED", "exit_code": 2, "evidence": []}
    print(json.dumps({"event": "attempt-start", **record}), flush=True)
    status = 2
    try:
        source = verify_snapshot(manifest, m)
        work = attempt / "work"
        for name in m["source"]["files"]:
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        if inventory(work) != m["source"]["files"]:
            raise ValueError("Fuentes cambiaron durante la copia a la tarea.")
        results = attempt / "results"
        results.mkdir()
        (work / "results").mkdir()
        for directory in ("home", "tmp"):
            (attempt / directory).mkdir()
        image = Path(c["image"])
        record["image_sha256"] = sha256(image)  # Solo en el nodo de cómputo.
        if c["image_sha256"] and c["image_sha256"] != record["image_sha256"]:
            raise ValueError("El SHA256 de la imagen no coincide.")
        runtime = shutil.which(c["runtime"])
        if not runtime:
            raise ValueError("Runtime no disponible tras cargar el módulo.")
        host_env = clean_host_env()
        record["runtime_version"] = subprocess.run([runtime, "--version"], env=host_env,
            capture_output=True, text=True, check=True).stdout.strip()
        record["node"] = os.environ.get("SLURMD_NODENAME", "")
        record["partition"] = os.environ.get("SLURM_JOB_PARTITION", "")
        record["cpus_per_task"] = os.environ.get("SLURM_CPUS_PER_TASK", "")
        record["allocated_cpus_on_node"] = os.environ.get("SLURM_CPUS_ON_NODE", "")
        base = [runtime, "exec", "--cleanenv", "--containall",
                "--bind", f"{work}:/work", "--bind", f"{results}:/work/results",
                "--bind", f"{attempt / 'home'}:/home/campaign",
                "--bind", f"{attempt / 'tmp'}:/tmp", "--pwd", "/work", str(image),
                "env", "-i", "PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME=/home/campaign", "TMPDIR=/tmp", "LANG=C.UTF-8",
                f"TB_SEED={task['seed']}", f"TB_CYCLES={m['cycles']}",
                f"COCOTB_RANDOM_SEED={task['seed']}", f"RANDOM_SEED={task['seed']}",
                "PYTHONHASHSEED=0", "PYTHONDONTWRITEBYTECODE=1",
                "CCACHE_DISABLE=1", f"MAKEFLAGS=-j{c['cpus']}",
                "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1", "RESULTS_DIR=/work/results"]

        def execute(command: list[str]) -> None:
            print(json.dumps({"command": command}), flush=True)
            subprocess.run(base + command, env=host_env, check=True)

        # Evita que los skipif de PyTest conviertan falta de simulador en éxito.
        execute(["python3", "-c", "import shutil, cocotb, pytest; "
                 "assert all(shutil.which(x) for x in ('verilator','iverilog','make')), "
                 "'Falta un simulador o make'; "
                 "print('cocotb', cocotb.__version__, 'pytest', pytest.__version__)"])
        execute(["verilator", "--version"])
        execute(["iverilog", "-V"])
        if m["mode"] == "demo":
            expected = []
            for sim in ("verilator", "icarus"):
                execute(["make", "sim", "DESIGN=counter", f"SIM={sim}", "WIDTH=8"])
                expected.append(results / f"cocotb-{sim}-counter-w8.xml")
        else:
            execute(["python3", "-m", "pytest", m["pytest_target"], "-m", "not hardware",
                     "-p", "no:cacheprovider", "--junitxml=/work/results/junit.xml",
                     "--html=/work/results/report.html", "--self-contained-html", "-v"])
            expected = [results / "junit.xml"]
            cocotb_xml = list(results.glob("cocotb-*.xml"))
            if not cocotb_xml:
                raise ValueError("La regresión no produjo evidencia de simulación CocoTB.")
            expected.extend(cocotb_xml)
        if not all(junit_valid(p) for p in expected):
            raise ValueError("Evidencia JUnit ausente, vacía, con fallos o skips.")
        record["evidence"] = [{"path": p.relative_to(attempt).as_posix(), "sha256": sha256(p)}
                              for p in expected]
        status = 0
        record["status"] = "COMPLETED"
    except subprocess.CalledProcessError as exc:
        # Conserva el fallo real de make/PyTest/runtime, incluidos los signals.
        status = exc.returncode if 0 < exc.returncode <= 255 else (
            128 - exc.returncode if -127 <= exc.returncode < 0 else 2)
        record["process_returncode"] = exc.returncode
        record["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
    finally:
        record["exit_code"] = status
        record["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(attempt / "task.json", record)
    return status


def timestamp(value: str) -> dt.datetime | None:
    try:
        result = dt.datetime.fromisoformat(value)
        return result.replace(tzinfo=dt.timezone.utc) if result.tzinfo is None else result
    except ValueError:
        return None


def duration(value: str) -> float | None:
    """SLURM CPU: [D-]HH:MM:SS[.sss] o MM:SS, sin convertir vacío en cero."""
    try:
        days, clock = value.split("-", 1) if "-" in value else ("0", value)
        pieces = clock.split(":")
        if len(pieces) not in (2, 3):
            return None
        result = float(days) * 86400
        for i, part in enumerate(reversed(pieces)):
            result += float(part) * 60**i
        return result if result >= 0 and math.isfinite(result) else None
    except ValueError:
        return None


def memory_bytes(value: str) -> float | None:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGTPE]?)", value, re.I)
    if not match:
        return None
    return float(match[1]) * 1024**("KMGTPE".index(match[2].upper()) + 1 if match[2] else 0)


def parse_sacct(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        values = [v.strip() for v in line.split("|")]
        if len(values) == len(FIELDS) + 1 and values[-1] == "":
            values.pop()
        if values == FIELDS:
            continue
        if len(values) != len(FIELDS):
            raise ValueError(f"Fila sacct inválida: se esperaban {len(FIELDS)} campos.")
        rows.append(dict(zip(FIELDS, values)))
    return rows


def state(value: str) -> str:
    return value.split()[0].rstrip("+") if value.strip() else "UNKNOWN"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def describe(values: list[float]) -> dict:
    return {"n": len(values), "p50": statistics.median(values) if values else None,
            "p95": percentile(values, .95), "p95_exploratory": len(values) < 20}


def seconds_between(end: str, start: str) -> float | None:
    a, b = timestamp(end), timestamp(start)
    if a is None or b is None:
        return None
    value = (a - b).total_seconds()
    return value if value >= 0 else None


def compressed_contains(identifier: str, job: str, index: int) -> bool:
    match = re.fullmatch(re.escape(job) + r"_\[([0-9,:%-]+)\]", identifier)
    if not match:
        return False
    for part in match[1].split("%", 1)[0].split(","):
        span = re.fullmatch(r"(\d+)(?:-(\d+)(?::(\d+))?)?", part)
        if not span:
            continue
        first, last, step = int(span[1]), int(span[2] or span[1]), int(span[3] or 1)
        if step and first <= index <= last and (index - first) % step == 0:
            return True
    return False


def evidence_for(root: Path, job: str, task: dict, manifest_hash: str, source_digest: str,
                 image_hash: str | None) -> tuple[bool, str]:
    directories = list((root / job / str(task["index"])).glob("attempt-*"))
    # Nunca escoger silenciosamente el mejor intento: una reejecución requiere análisis.
    # Un intento muerto por timeout puede no haber alcanzado a escribir task.json.
    if len(directories) != 1:
        return False, "missing-evidence" if not directories else "multiple-attempts"
    attempts = [directories[0] / "task.json"]
    try:
        record = load(attempts[0])
        if (record["status"] != "COMPLETED" or record["exit_code"] != 0 or
            record["manifest_sha256"] != manifest_hash or record["job_id"] != job or
            record["task"] != task["index"] or record["seed"] != task["seed"] or
            record["source_digest"] != source_digest or
            not re.fullmatch(r"[0-9a-f]{64}", record["image_sha256"]) or
            (image_hash and record["image_sha256"] != image_hash)):
            return False, "invalid-task-record"
        if not record["evidence"]:
            return False, "missing-junit"
        for artifact in record["evidence"]:
            path = (attempts[0].parent / artifact["path"]).resolve()
            if not path.is_relative_to(attempts[0].parent.resolve()):
                return False, "invalid-artifact-path"
            if sha256(path) != artifact["sha256"] or not junit_valid(path):
                return False, "invalid-junit"
        return True, "valid"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "invalid-task-record"


def summarize(m: dict, rows: list[dict[str, str]], job: str, manifest_hash: str,
              results_root: Path) -> dict:
    tasks = []
    by_id: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in rows:
        by_id[row["JobID"]].append(row)
    for task in m["tasks"]:
        jid = f"{job}_{task['index']}"
        allocations = by_id[jid]
        # sacct puede comprimir tareas aún pendientes: no inventar fechas ni completadas.
        pending = any(compressed_contains(r["JobID"], job, task["index"]) and state(r["State"]) == "PENDING"
                      for r in rows)
        item = {"job_id": jid, "index": task["index"], "seed": task["seed"],
                "state": "PENDING_UNRESOLVED" if pending else "MISSING",
                "queue_s": None, "execution_s": None, "total_s": None,
                "max_rss_bytes": None, "cpu_s": None, "cpu_efficiency": None,
                "success": False, "evidence": "missing-accounting"}
        if len(allocations) > 1:
            item["state"] = "AMBIGUOUS"
        elif allocations:
            row = allocations[0]
            status = state(row["State"])
            item.update(state=status, exit_code=row["ExitCode"], requested_memory=row["ReqMem"])
            item["queue_s"] = seconds_between(row["Start"], row["Submit"])
            if status not in ACTIVE:
                item["total_s"] = seconds_between(row["End"], row["Submit"])
                if row["ElapsedRaw"].isdigit():
                    item["execution_s"] = float(row["ElapsedRaw"])
            batch = by_id.get(jid + ".batch", [])
            # Steps no son tareas adicionales. MaxRSS es máximo observado por proceso,
            # no suma ni pico agregado del job. CPU del padre, fallback batch, nunca ambos.
            rss = [memory_bytes(r["MaxRSS"]) for key, group in by_id.items()
                   if key == jid or key.startswith(jid + ".") for r in group]
            item["max_rss_bytes"] = max((v for v in rss if v is not None), default=None)
            item["cpu_s"] = duration(row["TotalCPU"])
            if item["cpu_s"] is None and len(batch) == 1:
                item["cpu_s"] = duration(batch[0]["TotalCPU"])
            if item["execution_s"] and row["AllocCPUS"].isdigit() and int(row["AllocCPUS"]) > 0 and item["cpu_s"] is not None:
                item["cpu_efficiency"] = item["cpu_s"] / (item["execution_s"] * int(row["AllocCPUS"]))
            valid, reason = evidence_for(results_root, job, task, manifest_hash,
                                         m["source"]["digest"], m["config"]["image_sha256"])
            item["evidence"] = reason
            steps = [r for key, group in by_id.items() if key.startswith(jid + ".") for r in group]
            item["step_states"] = [{"job_id": r["JobID"], "state": state(r["State"]),
                                    "exit_code": r["ExitCode"]} for r in steps]
            steps_ok = all(state(r["State"]) == "COMPLETED" and r["ExitCode"] == "0:0" for r in steps)
            item["success"] = status == "COMPLETED" and row["ExitCode"] == "0:0" and valid and steps_ok
            item["timing_valid"] = all(item[k] is not None for k in ("queue_s", "execution_s", "total_s"))
            if item["timing_valid"] and item["queue_s"] > item["total_s"]:
                item["timing_valid"] = False
        tasks.append(item)
    counts = dict(collections.Counter(t["state"] for t in tasks))
    complete = all(t["state"] not in ACTIVE | {"MISSING", "AMBIGUOUS", "UNKNOWN",
                                             "PENDING_UNRESOLVED"} for t in tasks)
    success = sum(t["success"] for t in tasks)
    timing_complete = all(t.get("timing_valid", False) for t in tasks)
    relevant = [r for r in rows if r["JobID"] in {t["job_id"] for t in tasks}]
    starts = [timestamp(r["Submit"]) for r in relevant]
    ends = [timestamp(r["End"]) for r in relevant]
    window = None
    if complete and timing_complete and all(starts) and all(ends) and starts:
        window = (max(ends) - min(starts)).total_seconds()
    warnings = ["Carga sintética; no equivale a alumnado real.",
                "p95 interpolado linealmente; n < 20 es exploratorio, no prueba de capacidad.",
                "MaxRSS es el máximo observado por proceso/step, no RAM total del job.",
                "Sin registros de todos los intentos no se estima tasa flaky."]
    if not m["config"]["image_sha256"]:
        warnings.append("Imagen sin SHA256 fijado: no demuestra reproducibilidad 1:1 entre tareas.")
    if not complete:
        warnings.append("Campaña incompleta: throughput final y proporción final de fallos no disponibles.")
    return {"schema": 1, "job_id": job, "complete": complete,
            "manifest_sha256": manifest_hash,
            "source": {"git_sha": m["source"].get("git_sha"),
                       "dirty": m["source"].get("dirty"),
                       "digest": m["source"]["digest"]},
            "image_sha256": m["config"]["image_sha256"],
            "verdict": "PASS" if success == len(tasks) and timing_complete else "NOT_PASS",
            "timing_complete": timing_complete,
            "expected_tasks": len(tasks), "successful_tasks": success,
            "non_success_tasks": len(tasks) - success, "states": counts,
            "observed_terminal_failures": sum(t["state"] not in ACTIVE | {
                "MISSING", "AMBIGUOUS", "UNKNOWN", "PENDING_UNRESOLVED", "COMPLETED"} for t in tasks),
            "failure_fraction": (len(tasks) - success) / len(tasks) if complete else None,
            "latency_all_terminal": {key: describe([t[key] for t in tasks if t[key] is not None and
                                      t.get("timing_valid", False) and t["state"] not in ACTIVE |
                                      {"MISSING", "AMBIGUOUS", "PENDING_UNRESOLVED"}])
                                     for key in ("queue_s", "execution_s", "total_s")},
            "latency_success": {key: describe([t[key] for t in tasks if t["success"] and
                                              t.get("timing_valid", False) and t[key] is not None])
                                for key in ("queue_s", "execution_s", "total_s")},
            "window_submit_to_last_end_s": window,
            "throughput_success_per_minute": success * 60 / window if window and window > 0 else None,
            "warnings": warnings, "tasks": tasks}


def collect(args: argparse.Namespace) -> int:
    path = args.manifest.resolve()
    # Si sacct falla hoy, no dejar como resultado actual un PASS de ayer.
    # Las consultas anteriores permanecen en accounting/.
    write_json(path.parent / "summary.json", {
        "schema": 1, "verdict": "NOT_PASS", "complete": False,
        "error": "Recolección no finalizada; revisar el código de salida y el diagnóstico."})
    m = load(path)
    validate_manifest(m)
    receipt = load(path.parent / "submission.json")
    manifest_hash = sha256(path)
    if receipt.get("manifest_sha256") != manifest_hash:
        raise ValueError("El manifiesto cambió tras el envío.")
    job = receipt.get("job_id", "")
    if not re.fullmatch(r"\d+", job):
        raise ValueError("No hay job_id confirmado para recolectar.")
    if args.sacct_file:
        raw = args.sacct_file.read_text(encoding="utf-8")
    else:
        command = ["sacct", "--noheader", "--parsable2", "--jobs", job,
                   "--format=" + ",".join(f"{f}%80" if f in {"JobID", "State"} else f for f in FIELDS)]
        if receipt.get("cluster"):
            command += ["--clusters", receipt["cluster"]]
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=getattr(args, "query_timeout", 30),
                              env={**os.environ, "TZ": "UTC", "SLURM_TIME_FORMAT": "standard"}, check=True)
        raw = proc.stdout
    # Conserva cada consulta, incluyendo pendientes, sin borrar observaciones anteriores.
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    observation = {"queried_utc": stamp, "fields": FIELDS, "timezone": "UTC",
                   "raw": raw, "summary": None}
    observation_path = path.parent / "accounting" / f"{stamp}.json"
    write_json(observation_path, observation)
    summary = summarize(m, parse_sacct(raw), job, manifest_hash,
                        args.results_root or Path(m["config"]["results_root"]))
    observation["summary"] = summary
    write_json(observation_path, observation)
    write_json(path.parent / "summary.json", summary)
    if not getattr(args, "quiet", False):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def wait(args: argparse.Namespace) -> int:
    """Espera solo contabilidad/evidencia; nunca envía ni cancela trabajos."""
    positive(args.timeout_seconds, "timeout-seconds", 7200)
    positive(args.poll_seconds, "poll-seconds", 300)
    if args.poll_seconds < 5:
        raise ValueError("poll-seconds debe ser al menos 5 para no saturar SLURM.")
    deadline = time.monotonic() + args.timeout_seconds
    summary_path = args.manifest.resolve().parent / "summary.json"
    summary = None
    while True:
        if summary is not None and time.monotonic() >= deadline:
            summary["wait_status"] = "timeout"
            summary["verdict"] = "NOT_PASS"
            summary["warnings"].append(
                "Espera de contabilidad/evidencia agotada; no se cancela ni reenvía automáticamente.")
            write_json(summary_path, summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1
        query = argparse.Namespace(manifest=args.manifest, sacct_file=None,
                                   results_root=args.results_root, quiet=True,
                                   query_timeout=max(.001, min(30, deadline - time.monotonic())))
        try:
            status = collect(query)
            summary = load(summary_path)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            summary = {"schema": 1, "verdict": "NOT_PASS", "complete": False,
                       "error": str(exc), "wait_status": "collection-error"}
            write_json(summary_path, summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2
        remaining = deadline - time.monotonic()
        # SLURM puede terminar antes de que el filesystem compartido muestre
        # task.json/JUnit al login. Revalidar sin ampliar el plazo ni reenviar.
        awaiting_evidence = (
            summary["complete"] and summary["verdict"] != "PASS" and
            all(task["state"] == "COMPLETED" and task["exit_code"] == "0:0" and
                all(step["state"] == "COMPLETED" and step["exit_code"] == "0:0"
                    for step in task["step_states"]) and
                task["evidence"] in {"valid", "missing-evidence", "invalid-task-record",
                                     "missing-junit", "invalid-junit"}
                for task in summary["tasks"])
        )
        terminal = summary["complete"] and not awaiting_evidence
        if terminal or remaining <= 0:
            summary["wait_status"] = "terminal" if terminal else "timeout"
            if not terminal:
                summary["verdict"] = "NOT_PASS"
                summary["warnings"].append(
                    "Espera de contabilidad/evidencia agotada; no se cancela ni reenvía automáticamente.")
                status = 1
            write_json(summary_path, summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return status
        time.sleep(min(args.poll_seconds, remaining))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    plan_p = sub.add_parser("plan", help="Crear plan e instantánea, sin enviar jobs.")
    plan_p.add_argument("--config", type=Path, required=True)
    plan_p.add_argument("--source", type=Path, default=Path.cwd())
    plan_p.add_argument("--output", type=Path, required=True)
    plan_p.add_argument("--mode", choices=["regression", "demo"], default="regression")
    plan_p.add_argument("--batch-size", type=int, default=1)
    plan_p.add_argument("--repetitions", type=int, default=1)
    plan_p.add_argument("--concurrency", type=int, default=1)
    plan_p.add_argument("--seed", type=int, default=1234)
    plan_p.add_argument("--seed-mode", choices=["constant", "indexed"], default="constant")
    plan_p.add_argument("--cycles", type=int, default=200)
    plan_p.add_argument("--pytest-target", default="tests/counter")
    plan_p.set_defaults(func=plan)
    submit_p = sub.add_parser("submit", help="Mostrar argv; enviar solamente con --execute.")
    submit_p.add_argument("--manifest", type=Path, required=True)
    submit_p.add_argument("--execute", action="store_true")
    submit_p.add_argument("--allow-dirty", action="store_true")
    submit_p.add_argument("--require-idle", action="store_true",
                          help="Rechazar envío si hay cualquier job del usuario en cola.")
    submit_p.set_defaults(func=submit)
    collect_p = sub.add_parser("collect", help="Leer sacct y validar evidencia por tarea.")
    collect_p.add_argument("--manifest", type=Path, required=True)
    collect_p.add_argument("--sacct-file", type=Path)
    collect_p.add_argument("--results-root", type=Path, help="Raíz de resultados descargados, si se analiza localmente.")
    collect_p.set_defaults(func=collect)
    wait_p = sub.add_parser("wait", help="Esperar contabilidad con plazo finito; emitir un JSON final.")
    wait_p.add_argument("--manifest", type=Path, required=True)
    wait_p.add_argument("--results-root", type=Path)
    wait_p.add_argument("--timeout-seconds", type=int, default=600)
    wait_p.add_argument("--poll-seconds", type=int, default=15)
    wait_p.set_defaults(func=wait)
    worker = sub.add_parser("run-task", help="Interno: solo dentro de una asignación SLURM.")
    worker.add_argument("--manifest", type=Path, required=True)
    worker.add_argument("--mode", choices=["regression", "demo"], required=True)
    worker.set_defaults(func=run_task)
    value = sub.add_parser("config-value", help="Interno: inicialización del módulo.")
    value.add_argument("--manifest", type=Path, required=True)
    value.add_argument("--key", choices=["module_init", "module"], required=True)
    value.set_defaults(func=lambda a: print(load(a.manifest)["config"][a.key]) or 0)
    return p


def main() -> int:
    if os.name == "posix":
        os.umask(0o077)
    try:
        args = parser().parse_args()
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
