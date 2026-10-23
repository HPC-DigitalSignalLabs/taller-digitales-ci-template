# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Render CI artifacts as one human-readable Markdown report.

JUnit XML and Yosys JSON remain the machine-readable sources of truth.  This
module presents them by default; --require-pass also enforces their verdict.
It uses the Python standard library in every execution environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class TestSummary:
    path: Path
    tests: int
    passed: int
    failures: int
    errors: int
    skipped: int
    seconds: float

    @property
    def verdict(self) -> str:
        return "PASS" if self.tests > 0 and self.failures == self.errors == 0 else "FAIL"


@dataclass(frozen=True)
class SynthesisSummary:
    path: Path
    design: str
    width: str
    cells: int
    mistral_cells: int
    verdict: str


SIMULATION_NAME = re.compile(
    r"^cocotb-(?:(?P<sim>verilator|icarus)-)?(?P<design>.+?)"
    r"(?:-(?:width-?|w)(?P<width>\d+))?(?:-r(?P<rising>[01]))?\.xml$"
)
SYNTHESIS_NAME = re.compile(
    r"^yosys-(?P<design>.+?)-w(?P<width>\d+)(?:-r(?P<rising>[01]))?\.json$"
)


def parse_junit(path: Path) -> TestSummary:
    root = ElementTree.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    seconds = sum(float(case.get("time", "0") or 0) for case in cases)
    tests = len(cases)

    # Some JUnit producers emit only suite counters. Keep supporting those
    # files without double-counting nested <testsuite> elements.
    if not cases:
        suite = root if root.tag == "testsuite" else next(iter(root), root)
        tests = int(suite.get("tests", "0") or 0)
        failures = int(suite.get("failures", "0") or 0)
        errors = int(suite.get("errors", "0") or 0)
        skipped = int(suite.get("skipped", "0") or 0)
        seconds = float(suite.get("time", "0") or 0)

    return TestSummary(
        path=path,
        tests=tests,
        passed=tests - failures - errors - skipped,
        failures=failures,
        errors=errors,
        skipped=skipped,
        seconds=seconds,
    )


def parse_synthesis(path: Path) -> SynthesisSummary:
    match = SYNTHESIS_NAME.match(path.name)
    design = match.group("design") if match else path.stem
    width = match.group("width") if match else "no indicado"
    document = json.loads(path.read_text(encoding="utf-8"))
    modules = document.get("modules", {})
    preferred_top = design.replace("-", "_")
    module = modules.get(preferred_top)
    if module is None and len(modules) == 1:
        module = next(iter(modules.values()))
    cells = (module or {}).get("cells", {})
    mistral_cells = sum(
        str(cell.get("type", "")).startswith("MISTRAL_")
        for cell in cells.values()
    )
    return SynthesisSummary(
        path=path,
        design=(f"{design} (RISING={match.group('rising')})"
                if match and match.group("rising") is not None else design),
        width=width,
        cells=len(cells),
        mistral_cells=mistral_cells,
        verdict="PASS" if cells and mistral_cells else "FAIL",
    )


def _simulation_fields(path: Path) -> tuple[str, str, str] | None:
    match = SIMULATION_NAME.match(path.name)
    if not match:
        return None
    return (
        match.group("sim") or "no indicado",
        (f"{match.group('design')} (RISING={match.group('rising')})"
         if match.group("rising") is not None else match.group("design")),
        match.group("width") or "no indicado",
    )


def render_report(
    junit_paths: list[Path],
    synthesis_paths: list[Path],
    metadata: dict[str, str] | None = None,
    primary_junit: Path | None = None,
) -> str:
    warnings: list[str] = []
    tests: list[TestSummary] = []
    syntheses: list[SynthesisSummary] = []

    for path in sorted(set(junit_paths)):
        try:
            tests.append(parse_junit(path))
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            warnings.append(f"No se pudo leer `{path.name}`: {exc}")
    for path in sorted(set(synthesis_paths)):
        try:
            syntheses.append(parse_synthesis(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"No se pudo leer `{path.name}`: {exc}")

    # PyTest's top-level JUnit already includes the tests that launched CocoTB.
    # Use it for the headline totals and keep CocoTB XML as per-simulation
    # detail; summing both would count every simulation twice.
    named_primary = [
        item
        for item in tests
        if primary_junit is not None
        and item.path.resolve() == primary_junit.resolve()
    ]
    if not named_primary:
        named_primary = [item for item in tests if item.path.name == "junit.xml"]
    if not named_primary:
        named_primary = [
            item for item in tests if item.path.name == "junit-smoke.xml"
        ]
    primary_tests = named_primary or [
        item for item in tests if not item.path.name.startswith("cocotb-")
    ]
    primary_tests = primary_tests or tests

    total = sum(item.tests for item in primary_tests)
    passed = sum(item.passed for item in primary_tests)
    failures = sum(item.failures for item in primary_tests)
    errors = sum(item.errors for item in primary_tests)
    skipped = sum(item.skipped for item in primary_tests)
    seconds = sum(item.seconds for item in primary_tests)
    evidence_present = bool(tests or syntheses)
    verdict = (
        "PASS"
        if evidence_present
        and not warnings
        and failures == errors == 0
        and all(item.verdict == "PASS" for item in tests)
        and all(item.verdict == "PASS" for item in syntheses)
        else "FAIL"
    )

    lines = [
        "# Informe de verificación digital",
        "",
        f"- **Veredicto agregado:** `{verdict}`",
        f"- **Generado (UTC):** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- **Commit:** `{os.environ.get('GITHUB_SHA', 'no indicado')}`",
        "",
        "## Resumen de pruebas",
        "",
        "| Total | Aprobadas | Fallos | Errores | Omitidas | Tiempo acumulado |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {total} | {passed} | {failures} | {errors} | {skipped} | {seconds:.3f} s |",
        "",
        "### Artefactos JUnit",
        "",
        "| Archivo | Pruebas | Aprobadas | Fallos | Errores | Omitidas | Estado |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    if tests:
        lines.extend(
            f"| `{item.path.name}` | {item.tests} | {item.passed} | "
            f"{item.failures} | {item.errors} | {item.skipped} | `{item.verdict}` |"
            for item in tests
        )
    else:
        lines.append("| _No aplica a esta ejecución_ | 0 | 0 | 0 | 0 | 0 | `N/A` |")

    lines.extend(
        [
            "",
            "## Simulaciones",
            "",
            "| Simulador | Diseño | Parámetro WIDTH | Casos | Estado | Evidencia |",
            "|---|---|---:|---:|:---:|---|",
        ]
    )
    simulation_rows = [
        (item, fields)
        for item in tests
        if (fields := _simulation_fields(item.path)) is not None
    ]
    if simulation_rows:
        for item, (simulator, design, width) in simulation_rows:
            lines.append(
                f"| {simulator} | {design} | {width} | {item.tests} | "
                f"`{item.verdict}` | `{item.path.name}` |"
            )
    else:
        lines.append("| _No indicada_ | _No disponible_ | — | 0 | `N/A` | — |")

    lines.extend(
        [
            "",
            "## Síntesis lógica Yosys para Cyclone V",
            "",
            "| Diseño | WIDTH | Celdas | Celdas MISTRAL | Estado | Evidencia |",
            "|---|---:|---:|---:|:---:|---|",
        ]
    )
    if syntheses:
        lines.extend(
            f"| {item.design} | {item.width} | {item.cells} | "
            f"{item.mistral_cells} | `{item.verdict}` | `{item.path.name}` |"
            for item in syntheses
        )
    else:
        lines.append("| _No disponible_ | — | 0 | 0 | `N/A` | Ejecutar `make synth-all` |")
    lines.extend(
        [
            "",
            "> Yosys comprueba elaboración y mapeo lógico a celdas Cyclone V. "
            "No sustituye place-and-route, bitstream ni STA de Quartus.",
        ]
    )

    if metadata:
        lines.extend(
            [
                "",
                "## Metadatos SLURM",
                "",
                "| Campo | Valor |",
                "|---|---|",
            ]
        )
        lines.extend(f"| {key} | `{value}` |" for key, value in metadata.items())

    if warnings:
        lines.extend(["", "## Advertencias", ""])
        lines.extend(f"- {warning}" for warning in warnings)

    lines.extend(
        [
            "",
            "---",
            "Los XML JUnit y JSON Yosys siguen siendo la evidencia "
            "machine-readable; este Markdown es su vista humana.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output", type=Path, default=Path("results/report.md"))
    parser.add_argument("--junit", action="append", type=Path)
    parser.add_argument("--synthesis", action="append", type=Path)
    parser.add_argument(
        "--require-pass", action="store_true",
        help="falla si falta evidencia o hay pruebas fallidas, omitidas o síntesis rechazada",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="agrega todos los XML/JSON del directorio (solo para report.md)",
    )
    parser.add_argument(
        "--primary-junit",
        type=Path,
        help="JUnit que alimenta el resumen principal (evita usar evidencia anterior)",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="CAMPO=VALOR",
        help="metadato operativo, repetible (usado por SLURM)",
    )
    args = parser.parse_args(argv)

    metadata: dict[str, str] = {}
    for item in args.metadata:
        if "=" not in item:
            parser.error(f"metadata must use CAMPO=VALOR: {item!r}")
        key, value = item.split("=", 1)
        metadata[key] = value

    junit_paths = list(args.junit or [])
    synthesis_paths = list(args.synthesis or [])
    if args.discover:
        junit_paths.extend(args.results_dir.glob("*.xml"))
        synthesis_paths.extend(args.results_dir.glob("yosys-*.json"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_report(
            junit_paths,
            synthesis_paths,
            metadata,
            primary_junit=args.primary_junit,
        ),
        encoding="utf-8",
    )
    print(f"human-readable report: {args.output}")
    if args.require_pass:
        try:
            if not junit_paths and not synthesis_paths:
                raise ValueError("no verification evidence was requested")
            for path in junit_paths:
                summary = parse_junit(path)
                if summary.verdict != "PASS" or summary.skipped or summary.passed <= 0:
                    raise ValueError(f"{path.name}: required tests did not all pass")
            for path in synthesis_paths:
                if parse_synthesis(path).verdict != "PASS":
                    raise ValueError(f"{path.name}: synthesis evidence failed")
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
