# SPDX-License-Identifier: Apache-2.0
"""Estación Quartus nativa. Nunca programa ni configura servicios o controladores.

La salida JSON es evidencia local, no una certificación del equipo conectado.
No requiere paquetes externos. Las compilaciones viven en directorios nuevos y
no publican automáticamente artefactos en output_files/ del repositorio.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any


BOARDS = {
    "de1_soc": {
        "device": "5CSEMA5F31C6",
        "top": "de1_soc_top",
        "pinout": "tabla existente; pendiente de cotejo con la placa física",
    },
    "de10_standard": {
        "device": "5CSXFC6D6F31C6",
        "top": "de10_standard_top",
        "pinout": "sin verificar; QSF con pines pendientes",
    },
}
SOURCE_SPECS = [
    "src", "lib", "quartus", "tools/fpga_station.py",
    "tools/provenance.py", "tools/artifact_gate.py",
]
DEFAULT_BIN = r"C:\intelFPGA_lite\18.1\quartus\bin64"


class StationError(RuntimeError):
    """Fallo que impide respaldar el resultado con evidencia."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(argv: list[str], cwd: Path, timeout: float = 30, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Ejecutar un proceso acotado sin shell y conservar su resultado."""
    started = time.monotonic()
    try:
        process = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            env=env,
        )
        result = {
            "returncode": process.returncode,
            "stdout": process.stdout, "stderr": process.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        def text(value: bytes | str | None) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        result = {
            "returncode": None, "stdout": text(exc.stdout),
            "stderr": text(exc.stderr), "timed_out": True,
        }
    except OSError as exc:
        result = {
            "returncode": None, "stdout": "", "stderr": str(exc),
            "timed_out": False,
        }
    return {
        "argv": argv, "cwd": str(cwd), "duration_s": time.monotonic() - started,
        **result,
    }


def git(template: Path, *args: str) -> str:
    result = run(["git", "-C", str(template), *args], template)
    if result["returncode"] != 0:
        raise StationError(f"Git no pudo obtener evidencia: {result['stderr']}")
    return result["stdout"].strip()


def executable(bin_dir: Path, name: str) -> Path:
    return bin_dir / (name + (".exe" if os.name == "nt" else ""))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jtag_state(result: dict[str, Any]) -> str:
    """Enumeración no implica identificación comercial ni UART accesible."""
    text = result["stdout"] + "\n" + result["stderr"]
    if "no jtag hardware available" in text.lower():
        return "absent"
    if result["returncode"] == 0 and re.search(r"(?m)^\s*\d+\)\s+\S", text):
        return "enumerated"
    return "error_or_unrecognized"


def python_jtag_driver_check(bin_dir: Path, timeout: float) -> dict[str, Any]:
    """Comprobar símbolos y API del adaptador sin abrir una sesión JTAG.

    Solo importa el paquete: `setup_functions()` corre en la importación y
    resuelve cada símbolo con nombre decorado (`?jtagatlantic_open@@YA...`)
    contra las DLL instaladas. Un símbolo ausente o una DLL incompatible
    lanza `AttributeError`/`OSError` sin capturar, terminando el subproceso
    con código distinto de cero. Esto no abre una sesión JTAG: no se invoca
    ninguna función, solo se cargan las DLL y se ubican sus símbolos.
    """
    if importlib.util.find_spec("intel_jtag_uart") is None:
        return {"installed": False, "native_symbols_resolved": False, "adapter_api_resolved": False}
    probe = run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "import intel_jtag_uart; print('ABI_RESOLVED'); "
         "from jtag_stream import driver_api; driver_api(); print('ADAPTER_API_RESOLVED')",
         str(Path(__file__).resolve().parents[1] / "tests" / "common")],
        bin_dir, timeout,
        env={**os.environ, "QUARTUS_ROOTDIR": str(bin_dir.parent)},
    )
    resolved = "ABI_RESOLVED" in probe["stdout"].splitlines()
    compatible = probe["returncode"] == 0 and "ADAPTER_API_RESOLVED" in probe["stdout"].splitlines()
    return {"installed": True, "native_symbols_resolved": resolved,
            "adapter_api_resolved": compatible, "probe": probe}


def preflight(
    template: Path, bin_dir: Path, board: str | None, timeout: float, profile: str = "panel",
) -> dict[str, Any]:
    """Recoger versión y enumeración sin abrir el transporte de validación."""
    version = run([str(executable(bin_dir, "quartus_sh")), "--version"], template, timeout)
    jtag = run([str(executable(bin_dir, "jtagconfig"))], template, timeout)
    version_ok = version["returncode"] == 0 and bool(
        re.search(r"Version\s+18\.1(?:\.|\s)", version["stdout"] + version["stderr"])
    )
    state = jtag_state(jtag)
    driver = python_jtag_driver_check(bin_dir, timeout)
    target = dict(BOARDS[board]) if board in BOARDS else None
    if target is not None and profile == "jtag-counter":
        project_qsf(board, profile)
        target["top"] = "de1_soc_jtag_top"
    return {
        "schema": 1, "kind": "fpga_station_preflight", "created_at_utc": now(),
        "profile": profile,
        "host": {"system": platform.system(), "release": platform.release(),
                 "python": sys.version, "python_executable": sys.executable},
        "paths": {"template": str(template), "quartus_bin": str(bin_dir)},
        "board": {"selected_target": board, "physical_identity": "unconfirmed",
                  "target": target, "jtag_is_not_board_identification": True},
        "quartus": version, "quartus_18_1_detected": version_ok,
        "native_libraries": {
            name: {"path": str(bin_dir / name), "exists": (bin_dir / name).is_file()}
            for name in ("jtag_atlantic.dll", "jtag_client.dll")
        },
        "python_jtag_driver": driver,
        "jtag": {**jtag, "state": state},
        "can_attempt_compile_only": version_ok and board in BOARDS,
        "programming_supported": False,
        "hardware_validation_supported": False,
        "blockers": [
            "Falta confirmar modelo comercial, revisión y pinout de la placa.",
            *(["El perfil panel no contiene el canal JTAG; seleccione jtag-counter."] if profile == "panel" else []),
            "Falta validar operaciones y acuerdo simulación/silicio; resolver el API no demuestra transporte físico.",
            *([] if driver["adapter_api_resolved"] else ["No se resolvió el API del adaptador JTAG."]),
            *([] if driver["native_symbols_resolved"] else ["No se resolvieron los símbolos nativos del driver JTAG."]),
            *([] if state == "enumerated" else ["No se obtuvo una cadena JTAG reconocible."]),
            *([] if version_ok else ["No se confirmó Quartus 18.1."]),
        ],
    }


def snapshot(template: Path, workspace: Path, allow_dirty: bool) -> dict[str, Any]:
    """Congelar entradas, SHA y estado antes de invocar Quartus.

    Los blobs se calculan desde su ruta original para conservar la semántica de
    Git (incluidos filtros de fin de línea). SHA-256 comprueba la copia exacta.
    Se incluyen las entradas sin seguimiento; nunca se declaran árbol limpio.
    """
    root = Path(git(template, "rev-parse", "--show-toplevel")).resolve()
    commit = git(template, "rev-parse", "HEAD")
    status = git(template, "status", "--porcelain", "--untracked-files=all", "--", *SOURCE_SPECS)
    if status and not allow_dirty:
        raise StationError("Entradas sin confirmar en Git. Confirme el diseño o use --allow-dirty solo para exploración.")
    paths = git(template, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                "--", *SOURCE_SPECS).split("\0")
    sources: dict[str, str] = {}
    raw: dict[str, str] = {}
    for relative in sorted(set(paths) - {""}):
        source = template / relative
        if source.is_symlink() or not source.is_file():
            raise StationError(f"Entrada ausente o enlace no admitido: {source}")
        resolved = source.resolve()
        if not resolved.is_relative_to(template):
            raise StationError(f"Entrada fuera de la plantilla: {source}")
        key = resolved.relative_to(root).as_posix()
        digest = sha256(source)
        blob = git(template, "hash-object", "--", str(source))
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(target) != digest or sha256(source) != digest:
            raise StationError(f"Entrada modificada durante la copia: {source}")
        sources[key] = blob
        raw[relative] = digest
    if not sources:
        raise StationError("No hay entradas que compilar.")
    if git(template, "rev-parse", "HEAD") != commit:
        raise StationError("HEAD cambió durante la captura.")
    return {"commit": commit, "dirty": bool(status), "sources": sources,
            "raw_sha256": raw, "status": status, "repo_root": str(root)}


def project_qsf(board: str, profile: str) -> str:
    if profile not in ("panel", "jtag-counter"):
        raise StationError(f"Perfil no admitido: {profile}")
    if profile == "jtag-counter" and board != "de1_soc":
        raise StationError("jtag-counter solo admite el target de1_soc.")
    return f"quartus/{board}{'_jtag' if profile == 'jtag-counter' else ''}.qsf"


def prepare_project(workspace: Path, board: str, profile: str = "panel") -> dict[str, str]:
    """Usar el QSF versionado, no un generador que permita pines automáticos.

    Única transformación: quitar POST_FLOW_SCRIPT_FILE. La estación genera la
    procedencia desde esta captura previa, no desde el árbol vivo al terminar.
    El QSF transformado y el QPF quedan disponibles con sus hashes.
    """
    selected = project_qsf(board, profile)
    original = workspace / selected
    text = original.read_text(encoding="utf-8")
    expected = BOARDS[board]["device"]
    if not re.search(rf"(?m)^set_global_assignment -name DEVICE\s+\"?{expected}\"?\s*$", text):
        raise StationError("El DEVICE del QSF no coincide con el target seleccionado.")
    lines = [
        line for line in text.splitlines()
        if not re.match(r"\s*set_global_assignment\s+-name\s+POST_FLOW_SCRIPT_FILE\b", line)
    ]
    qsf = workspace / "station.qsf"
    qsf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    qpf = workspace / "station.qpf"
    qpf.write_text('QUARTUS_VERSION = "18.1"\nPROJECT_REVISION = "station"\n', encoding="utf-8")
    return {"selected_qsf": selected,
            "generated_qsf_sha256": sha256(qsf), "generated_qpf_sha256": sha256(qpf)}


def commands(
    template: Path, bin_dir: Path, board: str, workspace: Path, profile: str = "panel",
) -> dict[str, Any]:
    """Plan con argv explícitos; no incluye ninguna orden de programación."""
    report = workspace / "output_files" / "station.flow.rpt"
    return {
        "target_board": board, "device": BOARDS[board]["device"],
        "profile": profile, "selected_qsf": project_qsf(board, profile),
        "compile_cwd": str(workspace),
        "compile_argv": [str(executable(bin_dir, "quartus_sh")), "--flow", "compile", "station", "-c", "station"],
        "report": str(report),
        "gate_cwd": str(template),
        "gate_argv": [
            sys.executable, str(workspace / "tools" / "artifact_gate.py"),
            "--repo", str(template), "--report", str(report),
            "--commit", "HEAD",
            *[value for spec in SOURCE_SPECS for value in ("--source-glob", spec)],
        ],
        "note": "El ejecutor crea primero una captura nueva y un proyecto station; no ejecute compile en el árbol vivo.",
        "programming_supported": False,
    }


def compile_design(
    template: Path, bin_dir: Path, board: str, timeout: float, allow_dirty: bool,
    profile: str = "panel",
) -> tuple[dict[str, Any], int]:
    """Compilar una captura aislada. Nunca publicar un éxito por ausencia de datos."""
    project_qsf(board, profile)
    version = run([str(executable(bin_dir, "quartus_sh")), "--version"], template)
    if version["returncode"] != 0 or not re.search(r"Version\s+18\.1(?:\.|\s)", version["stdout"]):
        raise StationError("Esta ruta solo admite Quartus 18.1 comprobado.")
    parent = template / "results" / "fpga-station"
    parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="compile-", dir=parent))
    evidence: dict[str, Any] = {
        "schema": 1, "kind": "fpga_station_compile", "created_at_utc": now(),
        "workspace": str(workspace), "quartus": version, "board": board, "profile": profile,
        "physical_identity": "unconfirmed", "hardware_validated": False,
        "programmed": False, "status": "failed",
    }
    manifest = workspace / "station-evidence.json"
    try:
        inputs = snapshot(template, workspace, allow_dirty)
        evidence["inputs"] = inputs
        evidence["project"] = prepare_project(workspace, board, profile)
        plan = commands(template, bin_dir, board, workspace, profile)
        evidence["commands"] = plan
        execution = run(plan["compile_argv"], workspace, timeout)
        evidence["execution"] = execution
        # No proceso antiguo puede aportar reportes: workspace es nuevo.
        if execution["returncode"] != 0:
            raise StationError("Quartus no terminó correctamente; no se emite procedencia de éxito.")
        for relative, digest in inputs["raw_sha256"].items():
            if sha256(workspace / relative) != digest:
                raise StationError(f"Una entrada cambió durante la compilación: {relative}")
        output = workspace / "output_files"
        required = [output / f"station.{suffix}" for suffix in ("flow.rpt", "map.rpt", "sta.rpt", "sof")]
        if not all(path.is_file() and path.stat().st_size > 0 for path in required):
            raise StationError("Falta un artefacto no vacío: flow/map/sta/sof.")
        from artifact_gate import _report_says_success
        report = required[0]
        if not _report_says_success(report.read_text(encoding="utf-8", errors="replace")):
            raise StationError("El reporte no contiene Flow Status satisfactorio.")
        evidence["artifacts"] = {path.relative_to(workspace).as_posix(): sha256(path) for path in required}
        sidecar = {
            "schema": 1, "commit": inputs["commit"], "dirty": inputs["dirty"],
            "sources": inputs["sources"], "generated_at_utc": now(),
            "report": {
                "path": report.relative_to(Path(inputs["repo_root"])).as_posix(),
                "blob": git(template, "hash-object", "--", str(report)),
            },
            "tool": {"quartus_version": version["stdout"].strip(), "device": BOARDS[board]["device"],
                     "family": "Cyclone V", "revision": "station", "target_board": board,
                     "profile": profile, "station_manifest": str(manifest)},
        }
        report.with_suffix(".rpt.provenance.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        evidence["status"] = "compiled_dirty" if inputs["dirty"] else "compiled"
        evidence["gate"] = run(plan["gate_argv"], template)
        evidence["gate_accepted"] = evidence["gate"]["returncode"] == 0
        return evidence, 0 if evidence["gate_accepted"] else 1
    except (StationError, OSError) as exc:
        evidence["error"] = str(exc)
        return evidence, 1
    finally:
        manifest.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "plan", "compile", "gate"))
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--quartus-bin", type=Path, default=Path(os.environ.get("QUARTUS_BIN", DEFAULT_BIN)))
    parser.add_argument("--board", choices=sorted(BOARDS), help="Target de compilación; no identifica la placa.")
    parser.add_argument("--profile", choices=("panel", "jtag-counter"), default="panel")
    parser.add_argument("--timeout", type=float, default=30, help="Segundos por consulta o compilación.")
    parser.add_argument("--allow-dirty", action="store_true", help="Exploración solamente; el gate rechazará el artefacto.")
    parser.add_argument("--report", type=Path, help="Reporte existente para gate.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout debe ser finito y positivo")
    template = args.template.resolve()
    bin_dir = args.quartus_bin.resolve()
    try:
        if not (template / "quartus").is_dir():
            raise StationError("--template debe apuntar a la raíz de la plantilla.")
        if args.action == "preflight":
            if args.profile != "panel":
                project_qsf(args.board, args.profile)
            result = preflight(template, bin_dir, args.board, args.timeout, args.profile)
            # La preparación incompleta nunca es un verde de hardware.
            code = 1
        elif args.action == "gate":
            if args.report is None:
                parser.error("gate requiere --report")
            command = commands(template, bin_dir, args.board or "de1_soc", template)["gate_argv"]
            command[command.index("--report") + 1] = str(args.report.resolve())
            result = run(command, template, args.timeout)
            code = 0 if result["returncode"] == 0 else 1
        else:
            if args.board is None:
                parser.error("plan y compile requieren --board explícito; no se infiere del chip")
            if args.action == "plan":
                result = commands(template, bin_dir, args.board, template / "results" / "fpga-station" / "NUEVA_CAPTURA", args.profile)
                code = 0
            else:
                result, code = compile_design(template, bin_dir, args.board, args.timeout, args.allow_dirty, args.profile)
    except (StationError, OSError) as exc:
        result, code = {"schema": 1, "status": "error", "error": str(exc)}, 2
    # JSON ASCII evita depender de la página de códigos de Windows. Los archivos
    # de evidencia sí se guardan como UTF-8 y el lector recupera el mismo texto.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
