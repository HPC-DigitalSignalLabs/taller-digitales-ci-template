# digital-design-template

Plantilla de diseño y verificación digital para el curso **CE3201, Taller de Diseño
Digital** (TEC). Entregable del TFG *"Infraestructura híbrida de CI/CD para diseño y
verificación digital sobre FPGA"*.

Un estudiante clona este repositorio y escribe su diseño en `src/`. La plantilla
incluye simulación funcional automatizada, revisión de estilo y herramientas
para validar la procedencia de un reporte de síntesis. La validación física
requiere configurar la estación, confirmar la placa y completar el canal de
datos del diseño; no se obtiene solamente al clonar el repositorio.

**Plataforma:** configurable por `TARGET_BOARD` (`de1_soc` por defecto,
`de10_standard` opcional). Ver
`docs/decisions/ADR-0008-target-configurable.md`.

**Demo integrada:** guion completo,
esquema del sistema y
[fichas OpenCE de módulos](modules). El primer recorrido físico usa
el perfil `jtag-counter`; no implica cobertura en FPGA de toda la biblioteca.

Ese perfil completó una ejecución física el 2026-10-05 en DE1-SoC RevF0:
500 pasos y 1504 transacciones comparadas con éxito. El guion conserva la
referencia, el commit de compilación y el hash del SOF; repetirlo requiere
una nueva autorización de programación y evidencia vigente.

**Alcance vigente (2026-10-16):** el entregable se centra en un pipeline
reutilizable y robusto. El demo nuevo con MMIO, lógica secuencial, interfaz
humana y Python sigue pendiente de diseño; el contador anterior no lo
sustituye. JTAG deja de ser un objetivo independiente. Véase la
adenda del anteproyecto.

---

## Inicio rápido

```bash
git clone <este-repo> && cd digital-design-template

# Opción A, DevContainer (recomendada): abrir en VS Code y "Reopen in Container"
# Opción B, sin contenedor, solo las pruebas que no necesitan simulador:
python3 -m pip install -r .devcontainer/requirements.txt
make smoke
```

```bash
make smoke          # subconjunto sin simulador (objetivo <5 min en CI)
make test           # suite completa: JUnit para CI + Markdown/HTML para personas
make sim DESIGN=x   # Verilator + CocoTB para el diseño x
make designs        # enumera los nueve diseños nuevos y conservados
make library-regression # matriz RTL de biblioteca con Verilator e Icarus
make synth-all      # síntesis lógica abierta para Cyclone V con Yosys 0.40
make netlist DESIGN=x   # esquemático legible del RTL (results/netlist-*.svg)
make netlist-all     # vistas RTL de todos los bloques
make quartus-project TARGET_BOARD=de10_standard  # genera proyecto Quartus parametrizado
make report         # agregado informativo results/report.md (no es un gate)
make lint           # Verible (no bloqueante)
make gate           # valida el reporte de Quartus contra HEAD
make clean
```

---

## Biblioteca de referencia

Las cuatro familias vigentes se cubren con `sync_2ff`, `control_fsm`,
`enabled_register` y las dos conversiones `bin2bcd` / `bcd_to_7seg`.
`sync_2ff` sincroniza un bit, pero no es un filtro antirrebote.
Se conservan `counter`, `skid_buffer`, `alu` y `edge_detector` como diseños
auxiliares: no se reemplaza ni cambia su interfaz.

Cada bloque separa RTL, modelo Python, estímulos, comprobador y testbench.
Sus interfaces y límites están en las [fichas de módulos](modules).
Los targets de biblioteca, síntesis y netlist incluyen los nuevos diseños.

```bash
make library-test
make sim DESIGN=control_fsm
make sim DESIGN=enabled_register WIDTH=8
make sim DESIGN=bin2bcd WIDTH=8
make sim DESIGN=bcd_to_7seg
make library-regression
```

`library-regression` exige ambos simuladores; `make test` conserva las
omisiones explícitas cuando una herramienta opcional no está disponible.
El gatekeeper no acepta omisiones en la regresión obligatoria de biblioteca.
Ninguno de estos comandos programa una FPGA ni implementa el demo pendiente.

---

## Simuladores soportados por `make sim`

`make sim` usa `SIM=verilator` por defecto y también acepta `SIM=icarus`, ambos
dentro del dominio abierto que corre igual en el DevContainer, GitHub Actions y
Kabré.

Además, el template deja habilitado **`SIM=questa`** para quien tenga una
instalación local licenciada de Siemens Questa/ModelSim en la máquina del
laboratorio:

```bash
make sim SIM=questa DESIGN=counter
```

No hace falta cambiar `tests/*`: CocoTB selecciona el simulador mediante la
variable `SIM` y reutiliza el mismo `tb_<diseño>.py`, `stimulus.py`,
`model.py` y `checker.py`.

La frontera operativa se mantiene explícita: **`SIM=questa` es solo local**.
No se incorpora al DevContainer, a GitHub Actions ni a Kabré, porque esos
planos siguen restringidos al dominio abierto y reproducible del proyecto.

---

## Arquitectura: por dominio de software, no por ubicación

El eje que organiza todo es **qué software puede correr bajo licencia abierta y cuál no**,
no "nube contra local".

| Dominio | Qué corre | Dónde |
|---|---|---|
| **1. Verificación abierta** | Verilator, CocoTB, PyTest, Verible | Contenedor: máquina del estudiante, Kabré (CeNAT), *runners* de GitHub |
| **2. EDA propietario** | Quartus (síntesis, P&R, STA), Questa (gate-level) | Puesto de laboratorio, nativo. Licencia *node-locked* |
| **3. Validación en hardware** | Python + JTAG, openFPGALoader | Puesto de laboratorio, con la placa seleccionada por `TARGET_BOARD` |

La imagen del dominio 1 fija la cadena de herramientas. La equivalencia entre
entornos se comprueba ejecutando las mismas fuentes, configuración y semillas:
compartir imagen no garantiza por sí solo un resultado idéntico ante diferencias
de recursos o fallos de infraestructura.

Kabré nunca ejecuta EDA propietario. Esa es una regla obligatoria del
proyecto.

---

## Estructura

```
├── lib/                 cuatro familias vigentes y bloques auxiliares conservados
├── src/                 espacio del estudiante  ← tu diseño va aquí
├── tests/
│   ├── common/          capa de transporte agnóstica conservada; ver ADR-0009
│   ├── convention/      hace cumplir la convención de testbench (ADR-0002)
│   ├── gate/            pruebas de la compuerta de artefacto
│   ├── gatekeeper/      contratos del workflow y del veredicto agregado
│   └── <modulo>/        model.py · stimulus.py · checker.py · tb_<modulo>.py
├── tools/               procedencia, compuerta de artefacto y visualización de netlist
├── quartus/             proyecto Quartus estándar:
│   ├── digital_design_template.qpf  proyecto: una revisión por placa (ADR-0008)
│   ├── de1_soc.qsf / de10_standard.qsf  .qsf por revisión: device, pines y SDC
│   ├── boards/          top de placa (mapea el diseño a pines) + decodificador 7-seg
│   ├── targets/         restricciones SDC por placa
│   └── post_flow.tcl    copia el reporte y escribe la procedencia
├── hpc/                 trabajos SLURM para Kabré
├── output_files/        artefactos de síntesis + sidecar de procedencia
├── .devcontainer/       Dockerfile anclado + requirements
└── .github/workflows/   pipeline gatekeeper
```

El proyecto Quartus abre directamente en la GUI (`quartus/digital_design_template.qpf`)
**sin reconstrucción**: cada revisión (`de1_soc` por defecto, `de10_standard`) lleva su
`.qsf` con dispositivo, pines y SDC, y `TARGET_BOARD` selecciona la revisión. El pinout de
la DE10-Standard está **SIN VERIFICAR** (BL-011); el detalle está en `quartus/README.md`.

**Regla obligatoria:** `lib/` es material de referencia mantenido por el curso; `src/` es del
estudiante. No se mezclan.

---

## La convención de testbench

Cuatro piezas, cada una en su módulo. Es lo que sustituye a un framework tipo UVM, que
está **explícitamente fuera del alcance** de este proyecto.

| Archivo | Responsabilidad | Qué NO puede importar |
|---|---|---|
| `model.py` | Modelo de referencia en Python puro | cocotb, drivers |
| `stimulus.py` | Generación de estímulos, con semilla explícita | cocotb, drivers |
| `checker.py` | Comparación contra el modelo | **cocotb, drivers** |
| `tb_<modulo>.py` | Manejo de pines; delgado a propósito | No aplica |

La regla que sostiene todo: **el `checker` es agnóstico del transporte**.
Esto permite reutilizar comprobadores al cambiar de simulador o implementar
un transporte físico compatible; no demuestra que todos los bloques hayan
sido probados en FPGA. El objetivo independiente de JTAG pasó a recomendaciones
en la revisión de alcance del 2026-10-16.

La separación no se deja solo a revisión humana: `tests/convention/` analiza el AST
de cada módulo y **falla la compilación** si un `checker`, un `model` o un `stimulus`
importa cocotb o un driver. Corre dentro de `make smoke`, así que rompe el *build* en
segundos. Ver `docs/decisions/ADR-0002-capa-de-verificacion.md`.

Toda prueba es **determinista y con semilla registrada**:

```bash
make sim DESIGN=counter TB_SEED=1234   # cualquier fallo es reproducible
```

---

## El pipeline gatekeeper

El workflow del repositorio completo ejecuta los checks para PRs y candidatos
de cola de merge. `Gatekeeper verdict` solo acepta el éxito explícito de
`smoke` y `verification`; cualquier fallo, cancelación u omisión bloquea el
veredicto. La biblioteca debe completar su regresión Verilator/Icarus sin
omisiones, con XML validado mediante `human_report.py --require-pass`.
Véase el contrato de robustez y sus límites.

| # | Check | Dónde corre | Bloquea |
|---|---|---|---|
| 1 | Smoke y verificación funcional (Verilator + Icarus + CocoTB) | *Runner* de Actions; simulación en contenedor | Sí |
| 2 | Síntesis lógica abierta (`synth_intel_alm`) | Contenedor y *runner* | Sí |
| 3 | Lint y estilo (Verible) | *Runner* | No, por diseño |
| 4 | Validación del artefacto de síntesis Quartus | Validador local; job deshabilitado en gatekeeper | No en el workflow actual |
| 5 | Validación en hardware (`TARGET_BOARD`) | Estación del laboratorio, dispatch aprobado separado | No en PR |

Kabré usa un dispatch separado con campañas acotadas; no sustituye automáticamente
el job de verificación del PR. El workflow tampoco activa branch protections:
exigir el check, restringir bypass y proteger cambios al propio workflow son
tareas administrativas pendientes de comprobar según el plan de GitHub.

### Alcance de Yosys

Yosys 0.40 indica que Cyclone V utiliza `synth_intel_alm -family cyclonev`.
El comando `make synth-all` elabora el RTL, ejecuta la síntesis lógica, mapea
la red a celdas ALM `MISTRAL_*` y valida el JSON resultante para el contador,
la ALU con anchos de 8 y 32 bits, el skid buffer con anchos de 1, 8 y
32 bits, `sync_2ff`, ambas polaridades de `edge_detector` y los nuevos
`control_fsm`, `enabled_register`, `bin2bcd` y `bcd_to_7seg`.

Esta comprobación detecta RTL no sintetizable y errores estructurales con
software abierto. No realiza place-and-route, no genera un bitstream y no
produce resultados de timing. Quartus y su Timing Analyzer siguen siendo la
autoridad para esas etapas sobre el dispositivo concreto elegido por
`TARGET_BOARD`.

### Esquemático legible del RTL (`make netlist`)

`make netlist DESIGN=x` renderiza la lógica **elaborada** (antes del mapeo
tecnológico) como un esquemático legible en `results/netlist-*.svg`, para que el
estudiante *vea* el RTL que describió. Es una vista pedagógica complementaria a
`make synth`: **no** es place-and-route, bitstream ni STA, y Quartus sigue siendo
la autoridad. Necesita `yosys` y Graphviz `dot`, ambos provistos por el
DevContainer.

### Por qué existe el validador del artefacto Quartus

Quartus no puede correr en CI: su licencia es *node-locked*. CI recibe un reporte que no
puede regenerar. Un *gate* ingenuo haría `grep -q "Successful"` sobre el reporte, y eso
acepta esto:

```bash
printf 'Successful\n' > output_files/counter.flow.rpt   # pasa el gate ingenuo
```

Este template liga el reporte al commit. `quartus/post_flow.tcl` escribe un *sidecar*
`<reporte>.provenance.json` con el commit, el estado del árbol y el *blob* de cada fuente
compilada. `tools/artifact_gate.py` verifica siete condiciones y rechaza:

| Fallo | Código |
|---|---|
| Reporte o sidecar ausente | `MISSING_ARTIFACT` |
| Sidecar corrupto | `MALFORMED_PROVENANCE` |
| Generado con el árbol sucio | `DIRTY_TREE` |
| Generado en otro commit, o fuente cambiada, u omitida | `STALE_ARTIFACT` |
| Reporte editado después de generarse | `TAMPERED_REPORT` |
| Compilación fallida | `SYNTHESIS_FAILED` |

Todos los *hashes* son *blob ids* de `git hash-object`, calculados por git en ambos
extremos. No hay criptografía propia ni en TCL ni en Python.

**Limitación honesta:** el sidecar se genera en una máquina que el estudiante controla.
Esto defiende contra sustitución accidental y de bajo esfuerzo, no contra un falsificador
determinado. La solución completa es un *runner* auto-hospedado en el laboratorio que
genere el artefacto dentro de CI; está documentada como vía de mejora.

## Tablero estático para el profesor

ADR-0007 descarta un servicio permanente de *webhooks*: la observabilidad mínima
se obtiene consultando GitHub de forma programada y publicando un reporte
estático.

```bash
# GITHUB_TOKEN debe contener un token de instalación vigente.
make dashboard REPO=owner/name
make dashboard DASHBOARD_CONFIG=config/cohort.json ORG=curso-org
```

El colector genera `results/dashboard.md` y una captura JSON con actividad
acotada por ventana y presupuesto de consultas: commits, issues, PR, revisiones
y estado del workflow y sus jobs exactos. Distingue datos parciales, ausencia
de ejecuciones y errores; no calcula líneas de código ni convierte estas
señales en calificaciones.

La configuración limita owners, repositorios y nomenclatura. No descubre ni
autoriza repositorios solamente por su nombre. El workflow semanal se habilita
en un repositorio privado docente mediante `DASHBOARD_ENABLED`, configuración
revisada, ID y clave de la GitHub App. Emite un token temporal por instalación
y conserva resultados privados. Para preparar la organización y los permisos,
consultar `docs/github-app-setup.md` en el repositorio del proyecto.

---

## El canal host↔FPGA

Estímulos y *checkers* hablan contra una interfaz de transacciones única
(`tests/common/transport.py`), con backends intercambiables:

| Backend | Transporte | Estado |
|---|---|---|
| `MemoryTransport` | Ninguno (referencia) | Listo |
| `JtagUartTransport` | `altera_jtag_uart` + `intel_jtag_uart` | Primario |
| `SerialTransport` | UART sobre GPIO (pyserial) | Respaldo |
| `HpsBridgeTransport` | Puente ligero HPS-to-FPGA (`0xFF200000`) | Adicional |

`compare_logs()` entre una traza de simulación y una de hardware **es** la métrica de
acuerdo sim/hardware.

Los backends anteriores no implican que el canal físico esté validado. Los
tops de panel mantienen su comportamiento original; el perfil optativo
`jtag-counter` integra la IP JTAG UART, el decodificador W/R y el contador
controlado por reset/pasos. Se comprueba con Verilator e Icarus mediante
`python -m pytest tests/jtag_counter`.
En Windows se prefieren los comandos nativos
`python tools\fpga_station.py preflight --board de1_soc --profile jtag-counter`,
`plan --board de1_soc --profile jtag-counter` y
`compile --board de1_soc --profile jtag-counter --timeout 1800`.
Con Make y Bash están disponibles `make fpga-preflight`, `make fpga-plan` y
`make fpga-compile`, con `STATION_PROFILE=jtag-counter` para seleccionar ese
perfil (`panel` por defecto). Ninguno programa la placa; el target de
compilación no sustituye la identificación del hardware. El adaptador nativo,
el mapa de registros y el replay físico explícito están documentados en
`quartus/README.md`. No se declara acuerdo con silicio por compilar.

Nota de hardware: tanto la DE1-SoC como la DE10-Standard llevan
**USB-Blaster II** integrado. Por eso `pyftdi` y UrJTAG no son opciones para el cable
integrado; la automatización del laboratorio debe resolver el caso por `TARGET_BOARD`, no
asumir FTDI.

---

## Kabré (CeNAT)

```bash
python3 tools/hpc_campaign.py plan \
  --config hpc/campaign.example.json --source . \
  --output /work/$USER/campaigns/pilot-01 \
  --batch-size 1 --repetitions 1 --concurrency 1

# Revisión del comando; todavía no envía.
python3 tools/hpc_campaign.py submit \
  --manifest /work/$USER/campaigns/pilot-01/manifest.json

# Solo después de revisar recursos e imagen:
python3 tools/hpc_campaign.py submit \
  --manifest /work/$USER/campaigns/pilot-01/manifest.json --execute
python3 tools/hpc_campaign.py collect \
  --manifest /work/$USER/campaigns/pilot-01/manifest.json
```

Editar primero la configuración del entorno: imagen y SHA-256, partición,
cuenta, rutas y límites. La plantilla tiene valores de Kabré, no una
configuración universal. El envío rechaza fuentes modificadas salvo autorización
explícita para un piloto exploratorio con `--allow-dirty`.

Cada tarea conserva `task.json`, JUnit y reportes en un directorio independiente
por job, tarea e intento. El controlador verifica esos artefactos y produce
`summary.json` al recolectar. `--mode regression` ejecuta las pruebas del contador
por defecto; `--mode demo` ejecuta ese diseño con Verilator e Icarus. Ninguno
incluye síntesis de Quartus. Los pilotos no amplían automáticamente la carga.

Fuera de SLURM, los nombres distinguen el alcance:
`report-smoke.md`, `report-test.md`,
`report-sim-<simulador>-<diseño>-w<WIDTH>.md` y
`report-synth-all.md`. Antes de cada ejecución se eliminan solamente sus
fuentes y su reporte de destino; por eso un XML/JSON anterior no puede
contaminar el veredicto actual. `report.md` es únicamente el agregado
regenerable de todo lo disponible.

Solo particiones **Xeon** (`kura*`, `dribe*`); las `nukwa*` son GPU y este trabajo es
estrictamente CPU. Se separan tamaño del lote, límite de tareas concurrentes y
recursos por tarea. `kura-wide` anuncia 11 nodos, pero admite como máximo cuatro
por job; eso no equivale a la cuota disponible para el curso. El tiempo de cola
se mide por separado de la ejecución, sin asumir de antemano qué término domina.

**No construyas el `.sif` en un nodo de login.** Se construye en CI, se publica, y en el
clúster solo se hace `pull`.

---

## Reproducibilidad

La imagen base se identifica por digest, Verilator por versión y los paquetes
Python por versiones exactas. Esto reduce variación, pero no acredita que dos
construcciones produzcan una imagen idéntica. La campaña registra el hash de la
imagen ejecutada y la captura real de fuentes para contrastar los entornos.

Para subir una versión: cambiar **un** anclaje, correr la suite completa, y registrar el
resultado. Nunca relajar un anclaje para que una construcción pase.

---

## Licencia

**Apache-2.0** para todo el árbol (ver `LICENSE` y `NOTICE`).

Lo único que necesitás saber como estudiante: **tu diseño es tuyo.** Apache-2.0 es una
licencia permisiva, así que no te obliga a publicar nada de lo que escribas en `src/`.
Podés licenciar tu trabajo como quieras, o no licenciarlo.

Se descartó el copyleft (GPL) justamente por eso: sobre una plantilla que se clona para
hacer una tarea calificada, el copyleft se propagaría al trabajo del estudiante. Se eligió
Apache-2.0 y no MIT por la concesión expresa de patentes (§3). El razonamiento completo
está en `docs/decisions/ADR-0005-licenciamiento.md`.

> **Para quien mantenga la plantilla:** no incorporar código GPL/AGPL al árbol; eso
> reintroduciría el copyleft por la puerta de atrás. Invocar una herramienta copyleft como
> proceso aparte (Verilator, openFPGALoader) no es incorporación y es legítimo.

## Documentación

`docs/decisions/` (ADRs) · `docs/fase0/` (evidencia y observación de repos) ·
`docs/fase0/ruta-aprendizaje-herramientas.md` (ruta de aprendizaje del stack).
