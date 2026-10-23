# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
"""Promise / OE contract guard — the infrastructure's Definition-of-Done watchdog.

This module is the *meta* test suite. It does NOT re-test RTL behaviour (the
CocoTB testbenches already judge the counter, the skid buffer and the ALU under
simulation). Instead it maps every documented **promise** of the project to a
concrete, tool-free assertion that the infrastructure backing that promise
**exists and is wired**, so no promise can silently rot between commits. These
are existence/wiring guards, complementary to — never a substitute for — the
behavioural CocoTB tests.

Every test here is pure Python (file reads, AST parsing and pure-function calls)
and runs inside ``make smoke`` on a box with NO EDA tools installed
(no yosys/verilator/icarus/tclsh). Nothing shells out to yosys, verilator, make
or git.

What is guarded, and where it comes from
----------------------------------------
The authoritative source of the promises is ``docs/anteproyecto.md`` (OE1–OE4
and their acceptance table) and the root ``README.md`` ("Resultado que se
demuestra": the six technical facts).

The six technical facts (root README, "Resultado que se demuestra"):
  1. El entorno queda fijado en una sola imagen de contenedor.
  2. Verilator y CocoTB simulan RTL SystemVerilog con pruebas automatizadas.
  3. El skid buffer conserva datos y ``valid`` durante backpressure.
  4. Yosys 0.40 elabora y sintetiza la lógica para la arquitectura ALM de Cyclone V.
  5. GitHub Actions ejecuta las mismas comprobaciones como *pipeline gatekeeper*.
  6. Una organización del curso centraliza repositorios, permisos y estados.

The specific objectives (``docs/anteproyecto.md``):
  * OE1 — Evaluar metodologías EDA y arquitecturas de orquestación → requerimientos.
  * OE2 — Estructurar el pipeline que rutea simulación vs síntesis local → gatekeeper,
    reproducibilidad 1:1, tasa de *flaky* ≈ 0, gate del artefacto de síntesis.
  * OE3 — Construir diseños de referencia y el canal host↔FPGA → reúso de estímulos/
    checkers agnósticos del transporte, detección de divergencia inyectada.
  * OE4 — Validar mediante escenarios controlados (matriz *dry run* de 6 escenarios),
    escalabilidad y consistencia temporal en FPGA.

If a guard here fails, the linked promise/OE has regressed; each assertion names
the broken promise and points at the doc/ADR so the failure is self-explanatory.

Standalone-template robustness
------------------------------
The suite resolves everything from the template root (``parents[2]`` of this
file). Repo-root artifacts (the root README, ``.github/workflows``, ``docs/``)
live one level up in the monorepo but coincide with the template root in a
shipped standalone checkout, so those are probed in both locations and skipped
with a clear reason only when genuinely absent.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

# --- Path resolution ---------------------------------------------------------
# __file__ = <template>/tests/promises/test_design_promises.py
#   parents[0] = promises, parents[1] = tests, parents[2] = template root
TEMPLATE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = TEMPLATE_ROOT.parent  # monorepo root; == TEMPLATE_ROOT in standalone

TOOLS = TEMPLATE_ROOT / "tools"
TESTS = TEMPLATE_ROOT / "tests"

# Import the pure-Python tool helpers we exercise directly. Both modules import
# only stdlib, so importing them has no side effects and needs no EDA tools.
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _probe(*relparts: str) -> Path | None:
    """Return the first existing path for a repo-root-relative artifact.

    Probes the template root first, then its parent (the monorepo root). Returns
    ``None`` when the artifact exists in neither, so a genuinely standalone
    checkout can skip rather than fail.
    """
    rel = Path(*relparts)
    for base in (TEMPLATE_ROOT, REPO_ROOT):
        candidate = base / rel
        if candidate.exists():
            return candidate
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _imported_roots(path: Path) -> set[str]:
    """Top-level package name of every import in ``path`` (AST, not text search)."""
    tree = ast.parse(_read(path), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _defined_names(path: Path) -> set[str]:
    """Names bound at module scope by ``class``/``def``/assignment (AST)."""
    tree = ast.parse(_read(path), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# =============================================================================
# Fact 1 / OE2 — the environment is fixed in a single, fully pinned image.
# =============================================================================
DOCKERFILE = TEMPLATE_ROOT / ".devcontainer" / "Dockerfile"


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.is_file(), (
        "Fact 1 (single pinned image) broken: .devcontainer/Dockerfile is missing; "
        "the image is the unit of reproducibility (OE2 1:1 reproducibility)."
    )


def test_dockerfile_base_pinned_by_digest() -> None:
    """FROM must pin the base by @sha256: digest, never a floating tag (OE2)."""
    text = _read(DOCKERFILE)
    assert re.search(r"(?m)^FROM\s+\S+@sha256:[0-9a-f]{64}\b", text), (
        "Fact 1/OE2 broken: the Dockerfile FROM does not pin the base image by "
        "@sha256: digest. A floating tag reintroduces the version drift this "
        "infrastructure exists to eliminate (see Dockerfile header, ADR reproducibility)."
    )


@pytest.mark.parametrize(
    "arg",
    ["VERILATOR_VERSION", "IVERILOG_VERSION", "YOSYS_VERSION", "VERIBLE_VERSION"],
)
def test_dockerfile_toolchain_versions_pinned(arg: str) -> None:
    """Verilator/Icarus/Yosys/Verible each pinned via an ARG *_VERSION=."""
    text = _read(DOCKERFILE)
    assert re.search(rf"(?m)^ARG\s+{arg}=\S+", text), (
        f"Fact 1/OE2 broken: {arg} is not pinned via ARG in the Dockerfile; an "
        f"unpinned toolchain breaks 1:1 reproducibility across local/Kabré/runner."
    )


def test_dockerfile_graphviz_pinned() -> None:
    """graphviz (make netlist) is pinned via an exact apt version."""
    text = _read(DOCKERFILE)
    assert re.search(r"\bgraphviz=\S+", text), (
        "netlist-visualization promise broken: graphviz is not pinned to an exact "
        "apt version in the Dockerfile; `make netlist` renders with Graphviz `dot`."
    )


def test_dockerfile_final_run_verifies_versions() -> None:
    """The final RUN must fail the build if the pinned tools are not present."""
    text = _read(DOCKERFILE)
    for probe in ("dot -V", "yosys -V", "verilator --version"):
        assert probe in text, (
            f"Fact 1/OE2 broken: the Dockerfile does not verify {probe!r}; without "
            "the version self-check the image can ship a toolchain that is not what "
            "was pinned."
        )


# =============================================================================
# Fact 2 / OE1 — the testbench convention is enforced mechanically (ADR-0002).
# =============================================================================
CONVENTION_TEST = TESTS / "convention" / "test_testbench_convention.py"
MAINTAINED_MODULES = ["alu", "counter", "skid_buffer", "sync_2ff", "edge_detector",
                      "control_fsm", "enabled_register", "bin2bcd", "bcd_to_7seg"]


def test_convention_enforcer_exists() -> None:
    assert CONVENTION_TEST.is_file(), (
        "Fact 2/OE1 broken: tests/convention/test_testbench_convention.py is missing; "
        "the four-file convention (ADR-0002) is no longer enforced mechanically."
    )


@pytest.mark.parametrize("module", MAINTAINED_MODULES)
def test_maintained_module_is_convention_complete(module: str) -> None:
    """Each maintained module carries tb_*.py + model/stimulus/checker (ADR-0002)."""
    tb_dir = TESTS / module
    assert tb_dir.is_dir(), f"maintained testbench dir tests/{module}/ is missing"
    assert list(tb_dir.glob("tb_*.py")), (
        f"tests/{module}/ has no tb_*.py, so the convention enforcer will not even "
        f"discover it (ADR-0002)."
    )
    missing = [
        name
        for name in ("model.py", "stimulus.py", "checker.py")
        if not (tb_dir / name).exists()
    ]
    assert not missing, (
        f"Fact 2/OE1 broken: tests/{module}/ does not follow the ADR-0002 "
        f"convention; missing: {missing}"
    )


# =============================================================================
# OE3 — transport-agnostic stimulus/checker reuse (the sim↔hardware channel).
# =============================================================================
TRANSPORT = TESTS / "common" / "transport.py"


def test_transport_layer_exposes_the_reuse_api() -> None:
    """tests/common/transport.py must expose Transport, compare_logs, MemoryTransport."""
    assert TRANSPORT.is_file(), "OE3 broken: tests/common/transport.py is missing"
    names = _defined_names(TRANSPORT)
    for symbol in ("Transport", "compare_logs", "MemoryTransport"):
        assert symbol in names, (
            f"OE3 broken: tests/common/transport.py no longer defines {symbol!r}; "
            "the transport-agnostic reuse API (host↔FPGA channel) is the OE3 metric."
        )


@pytest.mark.parametrize(
    "proof",
    [
        TESTS / "common" / "test_transport.py",
        TESTS / "common" / "test_reuse_regmapped_design.py",
    ],
)
def test_transport_reuse_proofs_exist(proof: Path) -> None:
    assert proof.is_file(), (
        f"OE3 broken: the transport-reuse proof {proof.name} is missing; without it "
        "the stimulus/checker reuse metric is no longer demonstrated."
    )


def test_adr_0009_documents_the_reuse_decision() -> None:
    """ADR-0009 records why the transport layer exists (OE3, ADR-0004 D1)."""
    decisions = _probe("docs", "decisions")
    if decisions is None:
        pytest.skip("docs/decisions not present in this checkout (standalone template)")
    adrs = list(decisions.glob("ADR-0009*.md"))
    assert adrs, (
        "OE3 traceability broken: no docs/decisions/ADR-0009*.md; the transport-layer "
        "reuse decision is undocumented."
    )


# =============================================================================
# Fact 3 — the skid buffer preserves data and valid under backpressure.
# The behaviour is proven by CocoTB; here we guard that its checker stays
# transport-agnostic so the same checker can judge simulation AND the FPGA.
# =============================================================================
SKID = TESTS / "skid_buffer"
FORBIDDEN_IN_PURE_MODULES = {"cocotb", "serial", "intel_jtag_uart"}


def test_skid_buffer_testbench_is_complete() -> None:
    for name in ("model.py", "stimulus.py", "checker.py", "tb_skid_buffer.py"):
        assert (SKID / name).is_file(), (
            f"Fact 3 broken: tests/skid_buffer/{name} is missing; the backpressure "
            "property can no longer be driven and judged."
        )


def test_skid_buffer_checker_is_transport_agnostic() -> None:
    """checker.py must not import cocotb/serial/intel_jtag_uart (AST, ADR-0002/OE3)."""
    checker = SKID / "checker.py"
    assert checker.is_file(), "tests/skid_buffer/checker.py is missing"
    offending = _imported_roots(checker) & FORBIDDEN_IN_PURE_MODULES
    assert not offending, (
        "Fact 3/OE3 broken: tests/skid_buffer/checker.py imports "
        f"{sorted(offending)}; a transport-bound checker cannot be reused against "
        "the FPGA over JTAG, voiding the OE3 sim/hardware-agreement metric."
    )


# =============================================================================
# Fact 4 / OE2 — open logical synthesis maps to Cyclone V ALM (MISTRAL_*) cells.
# =============================================================================
MAKEFILE = TEMPLATE_ROOT / "Makefile"

# The six design/width combinations synth-all must cover (root README §5, Makefile).
SYNTH_ALL_COMBOS = [
    "yosys-counter-w8.json",
    "yosys-alu-w8.json",
    "yosys-alu-w32.json",
    "yosys-skid_buffer-w1.json",
    "yosys-skid_buffer-w8.json",
    "yosys-skid_buffer-w32.json",
]


def test_validate_netlist_rejects_a_netlist_without_mistral_cells(tmp_path: Path) -> None:
    """The Cyclone V gate must reject a netlist that was never ALM-mapped."""
    import check_yosys_netlist  # noqa: WPS433 (local import: needs sys.path set above)

    unmapped = tmp_path / "unmapped.json"
    unmapped.write_text(
        json.dumps({"modules": {"counter": {"cells": {"u0": {"type": "$dff"}}}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        check_yosys_netlist.validate_netlist(unmapped, top="counter")


def test_validate_netlist_accepts_a_mistral_mapped_netlist(tmp_path: Path) -> None:
    """The gate must accept a genuine Cyclone V ALM (MISTRAL_*) netlist (Fact 4)."""
    import check_yosys_netlist  # noqa: WPS433

    mapped = tmp_path / "mapped.json"
    mapped.write_text(
        json.dumps(
            {"modules": {"counter": {"cells": {"u0": {"type": "MISTRAL_FF"}}}}}
        ),
        encoding="utf-8",
    )
    # A correct netlist validates without raising.
    check_yosys_netlist.validate_netlist(mapped, top="counter")


def test_makefile_has_synth_all_target() -> None:
    text = _read(MAKEFILE)
    assert re.search(r"(?m)^synth-all:", text), (
        "Fact 4/OE2 broken: the Makefile has no `synth-all` target."
    )


@pytest.mark.parametrize("combo", SYNTH_ALL_COMBOS)
def test_makefile_synth_all_covers_combo(combo: str) -> None:
    """synth-all must enumerate all six design/width combos (root README §5)."""
    text = _read(MAKEFILE)
    assert combo in text, (
        f"Fact 4/OE2 broken: `make synth-all` no longer covers {combo}; the open "
        "Cyclone V synthesis check must span counter-w8, alu-w8/w32 and "
        "skid_buffer-w1/w8/w32."
    )


# =============================================================================
# NEW promise — readable RTL schematic (make netlist) is the pre-mapping view.
# =============================================================================
YOSYS_SHOW = TOOLS / "yosys_show.py"


def test_yosys_show_tool_exists() -> None:
    assert YOSYS_SHOW.is_file(), (
        "netlist-visualization promise broken: tools/yosys_show.py is missing."
    )


def test_yosys_show_script_is_pre_mapping_and_shows() -> None:
    """build_yosys_script must NOT run synth_intel_alm and MUST include `show`.

    The schematic is the readable *elaborated* view (pre technology mapping), the
    complement of the synth_intel_alm gate — mixing the two would produce an
    unreadable ALM graph instead of recognisable adders/muxes/registers.
    """
    import yosys_show  # noqa: WPS433

    script = yosys_show.build_yosys_script(
        sources=[Path("design.sv")], top="counter", width=8, dot_path=Path("out.dot")
    )
    assert "synth_intel_alm" not in script, (
        "netlist-visualization promise broken: build_yosys_script invokes "
        "synth_intel_alm; the readable schematic must stop BEFORE technology mapping."
    )
    assert "show" in script, (
        "netlist-visualization promise broken: build_yosys_script no longer emits a "
        "`show` command, so no schematic is rendered."
    )


def test_makefile_wires_netlist_target_to_yosys_show() -> None:
    text = _read(MAKEFILE)
    assert re.search(r"(?m)^netlist\b", text), (
        "netlist-visualization promise broken: the Makefile has no `netlist` target."
    )
    assert "tools/yosys_show.py" in text, (
        "netlist-visualization promise broken: the `netlist` target is not wired to "
        "tools/yosys_show.py."
    )


# =============================================================================
# NEW promise — the committed Quartus project opens and compiles unmodified.
# =============================================================================
QUARTUS = TEMPLATE_ROOT / "quartus"


def test_qpf_lists_both_board_revisions() -> None:
    """The .qpf declares both board revisions (ADR-0008)."""
    qpf = QUARTUS / "digital_design_template.qpf"
    assert qpf.is_file(), "Quartus promise broken: quartus/digital_design_template.qpf missing"
    text = _read(qpf)
    for revision in ("de1_soc", "de10_standard"):
        assert re.search(rf'PROJECT_REVISION\s*=\s*"{re.escape(revision)}"', text), (
            f"Quartus promise broken: the .qpf does not declare the {revision} "
            "revision (ADR-0008 revision-per-board)."
        )


def test_de1_soc_qsf_sets_device_and_top() -> None:
    """de1_soc.qsf targets the Cyclone V 5CSEMA5F31C6 with the board top (ADR-0003)."""
    qsf = QUARTUS / "de1_soc.qsf"
    assert qsf.is_file(), "Quartus promise broken: quartus/de1_soc.qsf missing"
    text = _read(qsf)
    assert re.search(r"-name\s+DEVICE\s+5CSEMA5F31C6\b", text), (
        "Quartus promise broken: de1_soc.qsf does not set DEVICE 5CSEMA5F31C6 "
        "(the ADR-0003 board device)."
    )
    assert re.search(r"-name\s+TOP_LEVEL_ENTITY\s+de1_soc_top\b", text), (
        "Quartus promise broken: de1_soc.qsf does not set TOP_LEVEL_ENTITY de1_soc_top."
    )


def test_de10_standard_qsf_marked_unverified() -> None:
    """de10_standard.qsf must carry the UNVERIFIED/BL-011 honesty marker."""
    qsf = QUARTUS / "de10_standard.qsf"
    assert qsf.is_file(), "Quartus promise broken: quartus/de10_standard.qsf missing"
    text = _read(qsf)
    assert "UNVERIFIED" in text and "BL-011" in text, (
        "Honesty promise broken: de10_standard.qsf no longer carries the "
        "UNVERIFIED (BL-011) marker; the DE10-Standard pinout is a placeholder."
    )


@pytest.mark.parametrize("top", ["de1_soc_top.sv", "de10_standard_top.sv"])
def test_board_tops_exist(top: str) -> None:
    assert (QUARTUS / "boards" / top).is_file(), (
        f"Quartus promise broken: quartus/boards/{top} (board wrapper to physical "
        "pins) is missing."
    )


def test_output_files_gitignore_keeps_tracked_evidence() -> None:
    """The artifact-gate evidence (*.flow.rpt + *.provenance.json) must stay tracked."""
    gitignore = None
    for candidate in (
        TEMPLATE_ROOT / "output_files" / ".gitignore",
        QUARTUS / "output_files" / ".gitignore",
    ):
        if candidate.is_file():
            gitignore = candidate
            break
    assert gitignore is not None, (
        "OE2 artifact-gate promise broken: no output_files/.gitignore found; the "
        "committed flow report and its provenance sidecar have no keep-rule."
    )
    for raw in _read(gitignore).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue  # negations re-include; that is what we want
        assert not line.endswith(".flow.rpt"), (
            f"OE2 broken: {gitignore} ignores the flow report ({line!r}); the "
            "artifact gate needs it tracked."
        )
        assert "provenance.json" not in line, (
            f"OE2 broken: {gitignore} ignores the provenance sidecar ({line!r})."
        )
        assert line not in ("*.rpt", "*.json"), (
            f"OE2 broken: {gitignore} rule {line!r} would swallow the tracked evidence."
        )


# =============================================================================
# Fact 5 / OE2 — GitHub Actions runs the same checks as the gatekeeper pipeline.
# =============================================================================
def _gatekeeper_yml() -> Path:
    wf = _probe(".github", "workflows", "gatekeeper.yml")
    if wf is None:
        pytest.skip(".github/workflows/gatekeeper.yml not present (standalone checkout)")
    return wf


@pytest.mark.parametrize("job", ["smoke", "verification", "gatekeeper"])
def test_gatekeeper_defines_required_jobs(job: str) -> None:
    text = _read(_gatekeeper_yml())
    assert re.search(rf"(?m)^  {job}:", text), (
        f"Fact 5/OE2 broken: the gatekeeper workflow no longer defines the {job!r} "
        "job."
    )


def test_gatekeeper_verdict_needs_smoke_and_verification() -> None:
    """The verdict job depends on both required checks (OE2 gate correctness)."""
    text = _read(_gatekeeper_yml())
    assert "needs: [smoke, verification]" in text, (
        "Fact 5/OE2 broken: the gatekeeper verdict no longer needs both smoke and "
        "verification; the merge gate would pass without them."
    )


def test_gatekeeper_actions_are_pinned_by_commit_sha() -> None:
    """Every `uses:` must pin a 40-hex commit SHA, never a floating @v tag (OE2)."""
    text = _read(_gatekeeper_yml())
    uses_lines = [
        ln.strip()
        for ln in text.splitlines()
        if re.search(r"\buses:\s*\S+", ln) and not ln.strip().startswith("#")
    ]
    assert uses_lines, "no `uses:` lines found; the workflow parse is wrong"
    unpinned = [
        ln for ln in uses_lines if not re.search(r"uses:\s*\S+@[0-9a-f]{40}\b", ln)
    ]
    assert not unpinned, (
        "OE2 hygiene broken: these `uses:` are not pinned by a 40-hex commit SHA "
        "(a tag is mutable and re-points):\n  " + "\n  ".join(unpinned)
    )


def test_gatekeeper_declares_permissions() -> None:
    text = _read(_gatekeeper_yml())
    assert re.search(r"(?m)^permissions:", text), (
        "OE2 hygiene broken: the gatekeeper workflow declares no top-level "
        "least-privilege `permissions:` block."
    )


# =============================================================================
# OE2 — the synthesis artifact gate rejects with the documented failure codes.
# =============================================================================
ARTIFACT_GATE = TOOLS / "artifact_gate.py"
GATE_FAILURE_CODES = [
    "MISSING_ARTIFACT",
    "MALFORMED_PROVENANCE",
    "DIRTY_TREE",
    "STALE_ARTIFACT",
    "TAMPERED_REPORT",
    "SYNTHESIS_FAILED",
]


def test_artifact_gate_exists() -> None:
    assert ARTIFACT_GATE.is_file(), (
        "OE2 broken: tools/artifact_gate.py (gatekeeper check 3) is missing."
    )


@pytest.mark.parametrize("code", GATE_FAILURE_CODES)
def test_artifact_gate_defines_failure_code(code: str) -> None:
    """Each dry-run rejection code (README table, OE4 scenarios 3–5) is present."""
    text = _read(ARTIFACT_GATE)
    assert code in text, (
        f"OE2/OE4 broken: tools/artifact_gate.py no longer references the {code!r} "
        "failure code; the dry-run rejection matrix would be incomplete."
    )


def test_artifact_gate_tests_exist() -> None:
    gate_dir = TESTS / "gate"
    assert gate_dir.is_dir() and list(gate_dir.glob("test_*.py")), (
        "OE2 broken: tests/gate/ has no test_*.py guarding the artifact gate."
    )


# =============================================================================
# Fact 6 / OE1 — centralized teacher observability (static dashboard, ADR-0007).
# =============================================================================
DASHBOARD = TOOLS / "dashboard.py"


def test_dashboard_tool_exposes_acceptance_rate() -> None:
    assert DASHBOARD.is_file(), "Fact 6 broken: tools/dashboard.py is missing"
    names = _defined_names(DASHBOARD)
    assert "compute_acceptance_rate" in names, (
        "Fact 6 broken: tools/dashboard.py no longer exposes compute_acceptance_rate; "
        "the cohort acceptance-rate metric (ADR-0007) is gone."
    )


def test_observability_tests_exist() -> None:
    obs = TESTS / "observability"
    assert obs.is_dir() and list(obs.glob("test_*.py")), (
        "Fact 6 broken: tests/observability/ has no test_*.py."
    )


def test_observability_reading_guide_exists() -> None:
    guide = _probe("docs", "observabilidad-docente.md")
    if guide is None:
        pytest.skip("docs/observabilidad-docente.md not present (standalone checkout)")
    assert guide.is_file()


# =============================================================================
# OE2 — determinism/seed: every run is reproducible from a logged seed.
# =============================================================================
def test_makefile_threads_a_default_tb_seed() -> None:
    """The Makefile defines a default TB_SEED and exports it (OE2 flaky ≈ 0)."""
    text = _read(MAKEFILE)
    assert re.search(r"(?m)^TB_SEED\s*\?=", text), (
        "OE2 flaky-rate promise broken: the Makefile defines no default TB_SEED; "
        "a run with no logged seed is not reproducible."
    )
    assert re.search(r"(?m)^export\s+TB_SEED\b", text), (
        "OE2 flaky-rate promise broken: the Makefile does not export TB_SEED, so the "
        "seed never reaches the testbench and failures are not replayable."
    )


# =============================================================================
# OE1 traceability — supervisor-feedback accounting and the decision backlog.
# =============================================================================
@pytest.mark.parametrize(
    "relparts",
    [
        ("docs", "feedback", "registro-retroalimentacion.md"),
        ("docs", "feedback", "README.md"),
        ("docs", "decisions", "backlog.md"),
    ],
)
def test_traceability_records_exist(relparts: tuple) -> None:
    found = _probe(*relparts)
    # These are research records of the TFG monorepo (template/ beside docs/);
    # an exported standalone template legitimately ships docs/ without them.
    if found is None and not (REPO_ROOT / "template").is_dir():
        pytest.skip("research records live in the TFG monorepo, not this checkout")
    assert found is not None, (
        "OE1 traceability broken: the record "
        f"{'/'.join(relparts)} is missing; supervisor feedback and the decision "
        "backlog must stay accounted for."
    )
