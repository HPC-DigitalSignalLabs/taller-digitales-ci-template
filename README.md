# taller-digitales-ci-template

[![gatekeeper](https://github.com/HPC-DigitalSignalLabs/taller-digitales-ci-template/actions/workflows/gatekeeper.yml/badge.svg?branch=main)](https://github.com/HPC-DigitalSignalLabs/taller-digitales-ci-template/actions/workflows/gatekeeper.yml)
[![Licencia: Apache-2.0](https://img.shields.io/badge/licencia-Apache--2.0-blue.svg)](LICENSE)

Plantilla abierta de **diseño y verificación digital con CI/CD** para cursos de
FPGA. Un equipo de estudiantes clona el repositorio, escribe RTL en `src/` y
cada pull request queda sometido a un **gatekeeper** reproducible: simulación
con dos simuladores abiertos, síntesis lógica abierta para Cyclone V,
evidencia JUnit obligatoria y un único veredicto que la rama principal exige.

Entregable del Trabajo Final de Graduación *Infraestructura híbrida de CI/CD
para diseño y verificación digital sobre FPGA* (Instituto Tecnológico de Costa
Rica, curso CE3201 Taller de Diseño Digital).

## Qué demuestra este repositorio

| Propiedad | Cómo se logra aquí |
|---|---|
| **Reproducible** | Una sola imagen OCI anclada (Ubuntu por digest, Verilator 5.028, Icarus 12, Yosys 0.40, CocoTB/PyTest por versión exacta). La misma imagen corre en el DevContainer, en GitHub Actions y, convertida a SIF, en el clúster Kabré. |
| **Modular** | Cada bloque de `lib/` trae RTL, modelo Python, estímulos, comprobador, testbench y ficha OpenCE. Agregar un bloque no requiere tocar el workflow. |
| **Configurable** | `TARGET_BOARD` (DE1-SoC / DE10-Standard), `SIM` (Verilator / Icarus / Questa local), `WIDTH` y parámetros por bloque, semilla `TB_SEED`, configuración HPC en JSON. |
| **Escalable** | Los PRs se evalúan en paralelo en runners efímeros; la cola de merge prueba cada combinación candidata antes de integrarla. Las campañas masivas se delegan a SLURM con límites explícitos. |
| **Abierto** | Apache-2.0. Todo el dominio de verificación es software libre; el EDA propietario (Quartus) queda en la estación del laboratorio y nunca en CI ni en el clúster. |

## Inicio rápido

```bash
git clone https://github.com/HPC-DigitalSignalLabs/taller-digitales-ci-template
cd taller-digitales-ci-template

# A) DevContainer (recomendado): abrir en VS Code -> "Reopen in Container"
# B) Sin contenedor, solo la parte sin simulador:
python3 -m pip install -r .devcontainer/requirements.txt
make smoke
```

Dentro del contenedor:

```bash
make designs              # bloques disponibles
make sim DESIGN=alu WIDTH=32 SIM=icarus
make library-regression   # matriz obligatoria Verilator + Icarus
make synth-all            # síntesis lógica abierta (synth_intel_alm, Cyclone V)
make netlist DESIGN=bin2bcd
```

La guía completa de la plantilla está en [docs/guia-plantilla.md](docs/guia-plantilla.md)
y las fichas de cada bloque en [docs/modules/](docs/modules/).

## El gatekeeper

```
PR / cola de merge
  ├─ Smoke (sin simulador, < 1 min) ......................... bloqueante
  ├─ Verificación funcional en la imagen anclada ............ bloqueante
  │    suite completa · biblioteca Verilator+Icarus · síntesis Yosys
  ├─ Lint Verible ........................................... informativo
  └─ Gatekeeper verdict = éxito explícito de smoke Y verificación
```

`Gatekeeper verdict` es el **único** check requerido por la rama `main`, de
modo que agregar jobs no exige editar las reglas. Un fallo, una cancelación,
una omisión o un estado desconocido de cualquier job obligatorio produce un
veredicto rojo. La evidencia (JUnit, reportes Markdown, JSON de Yosys) se
publica como artefacto incluso cuando la ejecución falla.

### Reglas de la rama `main`

- Todo cambio entra por pull request y por la **cola de merge**; no hay push
  directo, force-push ni borrado, tampoco para administradores.
- La cola reconstruye la combinación `main + PRs en cola` y vuelve a correr el
  gatekeeper sobre ella: detecta incompatibilidades entre PRs que pasan por
  separado.
- Cambios a `.github/` (el propio pipeline) requieren aprobación de un
  propietario de código ([CODEOWNERS](.github/CODEOWNERS)); una aprobación
  se invalida si llega un push posterior.
- Los PRs desde forks de colaboradores externos necesitan aprobación de un
  mantenedor antes de ejecutar workflows.

## Agregar un bloque a la biblioteca

Ver la lista completa en [CONTRIBUTING.md](CONTRIBUTING.md#agregar-un-bloque). En resumen:

1. `lib/<bloque>/<bloque>.sv`
2. `tests/<bloque>/` con `model.py`, `stimulus.py`, `checker.py`, `tb_<bloque>.py`
   y `test_<bloque>.py` (el comprobador no puede importar cocotb: lo impone
   `tests/convention/`).
3. Registrar el bloque en `LIBRARY_DESIGNS` y en los agregados del `Makefile`/`sim.mk`.
4. `docs/modules/<bloque>.md` con las secciones OpenCE (se verifican en CI).

## Componentes opcionales

Estos módulos existen en el código pero se activan solo en un despliegue
institucional, nunca en este repositorio público:

| Módulo | Dónde vive | Requisito |
|---|---|---|
| Campañas HPC en SLURM (`tools/hpc_campaign.py`, `hpc/`) | Clúster (Kabré, CeNAT) | Cuenta, SIF identificado por SHA-256, autorización |
| Estación FPGA nativa (`tools/fpga_station.py`, `quartus/`) | Puesto de laboratorio | Quartus con licencia local, placa confirmada |
| Tablero docente (`tools/dashboard.py`) | Repositorio privado docente | GitHub App instalada |

Los *runners* auto-hospedados **no** se conectan a este repositorio público;
ver [SECURITY.md](SECURITY.md).

## Licencia y créditos

Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)). El diseño que un
estudiante escriba en `src/` es suyo. La salida generada de Platform Designer
(IP JTAG UART de Intel/Altera) no se redistribuye: se regenera con
`quartus/ip/jtag_uart_system.tcl`. El archivo `EXPORT-PROVENANCE.json` liga
este árbol al commit del repositorio de investigación del que se exportó.
