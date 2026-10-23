# Proyecto Quartus del template

Esta carpeta contiene la **capa propietaria** del flujo: el proyecto de Intel
Quartus Prime que hace *place-and-route*, genera el *bitstream* y produce el
*timing* real sobre la tarjeta física. Es el dominio 2 de la arquitectura (EDA
propietario, licencia *node-locked*, corre nativo en el puesto de laboratorio),
nunca en Kabré ni en GitHub Actions.

**Frontera honesta:** la síntesis lógica abierta (`make synth-all`, Yosys) es una
comprobación estructural agnóstica de placa dentro de la familia Cyclone V. **No**
sustituye a Quartus: el P&R, el *bitstream* y el *timing* del dispositivo concreto
siguen siendo autoridad exclusiva de Quartus y su Timing Analyzer.

## Estado vigente: 5 de octubre de 2026 (S10, Fase 3; OE2/OE3)

**La compilación nativa sí funciona; el replay físico aún no está demostrado.**
El usuario confirmó **DE1-SoC RevF0**, fuente y USB de fábrica el 5 de octubre.
El coordinador reportó conexión y detección autorizada de solo lectura:
`DE-SoC [USB-1]`, HPS `4BA00477` primero y FPGA `02D120DD` segunda;
símbolos nativos y API resueltos. No hay autorización de programación.
Se cotejaron las doce asignaciones del perfil
contra las tablas 3-5/3-7/3-8 del manual oficial Rev F/G v2.0.4 (28-01-2019):
reloj, KEY0 y diez LEDs coinciden. La guía enlazada abajo conserva la fuente.
La programación y el replay necesitan aprobación aparte. Preflight sigue
devolviendo 1 y `hardware_validation_supported=false`; su metadato de top
para este perfil ahora indica correctamente `de1_soc_jtag_top`.
En `445d0df`, antes de editar esta documentación, las 33 entradas del subconjunto
de estación estaban limpias aunque el resto del repositorio tenía cambios.
La captura aislada `results/fpga-station/compile-ih76tih9` terminó en
`compiled`, `gate_accepted=true` (115,30 s), con Quartus Lite 18.1 Build 625.
SOF SHA-256: `6e796898ddf23c34d85a5ea92fbf734559d546177aa1cfc9c92d679ceca707ce`.
Esto supera la antigua evidencia exploratoria `DIRTY_TREE`, pero no equivale
a autorización de programación ni a validación física.

La estación incluye **todo** `src`, `lib`, `quartus` (también este README) y
`tools/fpga_station.py`, `tools/provenance.py`, `tools/artifact_gate.py`.
Los cambios ajenos no bloquean por sí mismos. Las modificaciones de cualquiera
de esas entradas sí exigen confirmarlas en Git, por decisión del usuario, y
generar una captura nueva. No se debe usar el SOF anterior como evidencia de
los cambios actuales. Consultar el subconjunto desde la raíz del template:

```powershell
git status --short --untracked-files=all -- src lib quartus tools/fpga_station.py tools/provenance.py tools/artifact_gate.py
```

El procedimiento vigente de conexión, programación explícita y replay está en
`docs/primera-demostracion-fpga.md`.
**No ejecutar `preflight` antes del checkpoint de hardware:** consulta
`jtagconfig`, aunque no programa ni abre UART. `plan`, `compile` y replay
`check` no consultan la placa. No se consultó ni modificó hardware en esta
comprobación.

## Evidencia histórica del 29 de septiembre de 2026

**No está listo para programación extremo a extremo.** El usuario identificó
la placa como DE1-SoC Altera Cyclone V, chip `5CSEMA5F31C6N`; falta la revisión
de PCB y el cotejo del pinout contra su manual. El sufijo del chip no es la
revisión de placa. La [guía oficial de Terasic](https://www.terasic.com.tw/cgi-bin/page/archive.pl?Language=English&No=886)
identifica las marcas `B0` a `H0` como revisiones B a H.

| Requisito | Evidencia / estado |
|---|---|
| Quartus nativo | Lite 18.1.0 Build 625 responde correctamente |
| Cadena JTAG | `DE-SoC [USB-1]`, `4BA00477 SOCVHPS`, `02D120DD 5CSE(BA5\|MA5)/5CSTFD5D5/..` |
| Driver Python | API nativa resuelta; adaptador de conteos reales y timeout probado con dobles, no con silicio |
| API del transporte | Keywords reales, buffers mutables y firmas C corregidas; proceso aislado por sesión |
| IP JTAG UART | Receta regenerada; QIP e instancia integrados en `de1_soc_jtag.qsf` / `de1_soc_jtag_top.sv` |
| Decodificador W/R | Simulado con Verilator e Icarus: backpressure, fragmentación, reset, dirección completa, timeout y fallo persistente |
| Control determinista del DUT | `src/counter.sv` real: reset, un paso por comando y lectura estable; sin contador libre |
| Restricciones | Perfil nuevo sin SW/HEX: reset sincronizado, reloj 50 MHz y excepciones limitadas; faltan márgenes externos del JTAG dedicado |
| Síntesis / hardware en CI | Ambos jobs siguen deshabilitados con `if: false`; un verdict verde solo cubre los gates abiertos habilitados |
| Acuerdo simulación/silicio | Sin medición; no se programó la FPGA en esta recuperación |

El preflight distingue `python_jtag_driver.native_symbols_resolved` de
`adapter_api_resolved`; ninguno certifica operaciones sobre silicio. Sigue
devolviendo código 1 mientras la validación física no esté comprobada.

El gatekeeper `36617056046` falló por 14 pruebas del transporte: se publicaron
las pruebas sin el cambio correspondiente de `tests/common/hardware.py`.
La recuperación incluye ese cambio y la receta de simulación que invoca
explícitamente `sim`, conserva el código de error y rechaza evidencia vacía
o ausente. No se deshabilitan pruebas ni se habilitan gates de hardware sin
evidencia.

## Perfil optativo JTAG-counter

La estación selecciona `--profile jtag-counter --board de1_soc`. El perfil
predeterminado `panel`, los QSF anteriores y sus demos permanecen intactos.
Este hito cubre **un contador**, no el requisito N/N de toda la biblioteca.
`LEDR[7:0]` muestra la cuenta, `LEDR[8]` el reset liberado y `LEDR[9]` un fallo
persistente de protocolo. KEY0 reinicia simultáneamente el canal y el diseño;
su liberación pasa por dos flip-flops. La placa no utiliza SW, HEX, GPIO ni
memorias externas en este perfil.

Direcciones de **palabra**, enteras de 32 bits, sin truncamiento:

| Dirección | Operación | Significado |
|---|---|---|
| 0 | R | Identidad `0x434E5431` (`CNT1`) |
| 1 | W, valor 1 | Reset de contador y número de pasos; no resincroniza un canal roto |
| 2 | W, valor 0 o 1 | Un paso determinista; enable es el valor escrito |
| 3 | R | Cuenta de 8 bits, extendida a 32 bits |
| 4 | R | Número de pasos desde reset, módulo 2³²; incluye pasos con enable=0 |

Todo acceso inválido detiene el canal hasta KEY0/reconfiguración. También se
detiene ante un comando desconocido, un frame incompleto o una operación sin
progreso durante 50 millones de ciclos (1 segundo a 50 MHz). No se omiten bytes
para intentar resincronizar y no hay ACK/CRC. El transporte host queda
invalidado tras una excepción o timeout. Una lectura perdida no se reintenta.

Desde la raíz del template, con el entorno abierto:

```bash
JTAG_EXPORT_DIR=results/jtag-reference-NUEVA-CORRIDA python3 -m pytest tests/jtag_counter -q --basetemp=/tmp/jtag-demo-NUEVA-CORRIDA --junitxml=results/jtag-demo-NUEVA-CORRIDA.xml
```

Ejecuta ambos simuladores y conserva `jtag-counter-verilator.json` y
`jtag-counter-icarus.json` en el `results/` de cada caso dentro de `--basetemp`,
junto con sus XML CocoTB. Usar una ruta nueva: PyTest limpia `--basetemp` al
iniciar. No se sobrescriben las referencias anteriores en `results/`.
La variable explícita `JTAG_EXPORT_DIR` exporta los JSON recién producidos
y sus XML a una ruta estable **solo después de aprobar cada suite**; rechaza
destinos existentes. Sin ella, no se generan trazas en la raíz de `results/`.
El modelo del esclavo
Avalon en CocoTB **no** modela USB ni sustituye a la IP Intel en silicio.
Los hashes de fuentes de la traza normalizan CRLF/LF a UTF-8 con LF
(`source_format=sha256-utf8-lf`); la captura Quartus conserva además hashes
de sus bytes originales. La referencia incluye las dependencias compartidas
`tests/counter/model.py`, `tests/counter/stimulus.py` y
`tests/common/transport.py`: una modificación de modelo, estímulos o
comparación de transacciones invalida la traza. Regenerar las referencias
antiguas que no incluían esos hashes.
El generador reutiliza `counter/stimulus.py` y el modelo `counter/model.py`;
el mismo checker de transacciones se usa en RTL y en replay.
`make sim` exige un XML sin fallos, errores ni casos omitidos mediante
`human_report.py --require-pass`: el código de salida cero del simulador por
sí solo no demuestra éxito en CocoTB 1.9.
PyTest sella cada traza con el hash de su XML solo después de comprobar los
cinco escenarios obligatorios. Para replay deben conservarse juntos JSON y
XML; una traza aislada, sin suite completa y aprobada, se rechaza.

En la estación Windows, sin abrir UART ni programar:

```powershell
python -m pip install -r requirements-hardware.txt
$env:QUARTUS_ROOTDIR = 'C:\intelFPGA_lite\18.1\quartus'
python tools\fpga_station.py plan --board de1_soc --profile jtag-counter
python tools\fpga_station.py compile --board de1_soc --profile jtag-counter --timeout 600
# Seleccionar el JSON nuevo; su XML CocoTB debe permanecer al lado.
python tools\jtag_counter_replay.py check --reference RUTA-REFERENCIA.json
```

La compilación normal exige fuentes confirmadas; `--allow-dirty` solo produce
evidencia exploratoria que el gate rechaza. Quartus se mantiene fuera de Docker,
GitHub Actions y Kabré. La primera integración exploratoria compiló correctamente
en `results/fpga-station/compile-t0bybp4j`, sin programar, y fue rechazada por
`DIRTY_TREE`, como corresponde.

STA de esa captura obtuvo peor setup/hold interno de `clk_50` de **14,087 ns /
0,118 ns** entre sus cuatro esquinas. No se convierten en una afirmación de
cierre del sistema: permanecen sin restricciones externas los puertos
dedicados `altera_reserved_tdi`, `altera_reserved_tms` y `altera_reserved_tdo`.
El cable consultado indicó **24 MHz**; no se cambió su frecuencia ni se
inventaron retardos USB/JTAG. Las advertencias de excepciones internas no
coincidentes de Quartus 18.1 quedan conservadas en los reportes. Los LEDs no
tienen receptor síncrono y están excluidos explícitamente, no ocultos como
salidas sin restricciones. Sus parámetros eléctricos hacen explícitos los
valores ajustados por Quartus (16 mA, slew 1), todavía sujetos al cotejo físico.

**Solo después de la revisión física, autorización de programación y carga
manual del SOF exacto:** cerrar otras sesiones UART, pulsar/liberar KEY0 y
ejecutar el replay explícito. Sustituir `CAPTURA` y `REVISION` por los valores
reales, nunca por el ejemplo:

```powershell
python tools\jtag_counter_replay.py run `
  --reference RUTA-REFERENCIA.json `
  --station-manifest results\fpga-station\CAPTURA\station-evidence.json `
  --board-revision REVISION --confirm-board-ready `
  --cable 'DE-SoC [USB-1]' --timeout 2 `
  --output results\jtag-counter-hardware.json
```

El comando no programa. Rechaza referencia obsoleta/corrupta, fuentes
modificadas, bitstream alterado, perfil distinto y procedencia sucia antes de
abrir el cable. Conserva errores y la traza parcial; `transaction_agreement_passed`
solo significa acuerdo de esas transacciones. El hash del SOF identifica el
archivo de la estación; **no demuestra qué bitstream está cargado**. Esa parte
sigue siendo una confirmación del operador (`configured_bitstream_verified=false`).
La ingestión automática del resultado por el colector, el gate físico, las
medidas de timing físico y el mutation kill rate ≥80% siguen pendientes.

## Estructura estándar de proyecto Quartus

```
quartus/
├── digital_design_template.qpf   proyecto (.qpf): declara ambas revisiones
├── de1_soc.qsf                   settings (.qsf) revisión DE1-SoC   (5CSEMA5F31C6)
├── de10_standard.qsf             settings (.qsf) revisión DE10-Standard (5CSXFC6D6F31C6)
├── boards/
│   ├── de1_soc_top.sv            top de placa DE1-SoC: mapea `counter` a pines reales
│   ├── de10_standard_top.sv      top de placa DE10-Standard (pinout SIN VERIFICAR, BL-011)
│   └── seg7_hex.sv               decodificador 7-segmentos compartido (activo-bajo)
├── targets/
│   ├── de1_soc.sdc               restricciones SDC DE1-SoC
│   └── de10_standard.sdc         restricciones SDC DE10-Standard
├── de1_soc.sdc                   SDC heredado (compatibilidad; preferir targets/)
├── build_project.tcl            generador programático del proyecto
└── post_flow.tcl                post-flow: copia el reporte y escribe la procedencia

../output_files/                  artefactos de compilación (en la RAÍZ del template,
                                  no dentro de quartus/; ver más abajo)
```

`output_files/` vive en la **raíz del template** (junto a `src/`, `lib/` y
`quartus/`), no dentro de `quartus/`. Así convergen en un único directorio la
compuerta de artefacto (`REPORT=output_files/…`), el trabajo `artifact-gate` de
CI, `post_flow.tcl` (`<raíz-git>/output_files`) y el `PROJECT_OUTPUT_DIRECTORY`
de cada `.qsf`, que se resuelve desde esa misma raíz.

## Qué se versiona y qué se genera

| Archivo | ¿Versionado? | Por qué |
|---|---|---|
| `.qpf`, `.qsf` | Sí | Definen el proyecto; permiten abrirlo directo en Quartus |
| `boards/*.sv`, `targets/*.sdc` | Sí | Material de referencia mantenido por el curso |
| `build_project.tcl`, `post_flow.tcl` | Sí | Automatización del flujo |
| `output_files/<rev>.flow.rpt` | **Sí (excepción)** | Evidencia que la compuerta de CI verifica contra el commit |
| `output_files/<rev>.flow.rpt.provenance.json` | **Sí (excepción)** | *Sidecar* de procedencia que exige la compuerta |
| Resto de `output_files/` (`db/`, `*.sof`, `*.rbf`, `*.map.*`, `*.fit.*`, `*.sta.*`, …) | No | Binarios grandes y regenerables |

La excepción de los dos archivos rastreados es deliberada: Quartus **no** puede
correr en CI (licencia *node-locked*), así que CI recibe un reporte que no puede
regenerar y lo valida contra el commit. Ver `quartus/post_flow.tcl`,
`tools/artifact_gate.py` y `docs/decisions/ADR-0006-*`. El `.gitignore` de
`output_files/` ignora los productos de compilación pero **nunca** `*.flow.rpt`
ni `*.provenance.json`.

## `.qpf`/`.qsf`/top de placa: la convención

- El **`.qpf`** (`digital_design_template.qpf`) declara dos **revisiones**, una por
  tarjeta (ADR-0008): `de1_soc` (por defecto) y `de10_standard`.
- Cada revisión tiene su **`.qsf`** con `DEVICE`, `FAMILY`, `TOP_LEVEL_ENTITY`, el
  `SDC_FILE` de su placa, el `POST_FLOW_SCRIPT_FILE` de procedencia,
  `PROJECT_OUTPUT_DIRECTORY output_files`, los `SEARCH_PATH` a `src/` y `lib/`, las
  fuentes SystemVerilog y las asignaciones de pines.
- El **top de placa** (`boards/<target>_top.sv`) instancia el diseño de referencia
  `counter` y lo conecta a los puertos físicos (`CLOCK_50`, `KEY[3:0]` activo-bajo,
  `SW[9:0]`, `LEDR[9:0]`, `HEX0..HEX5`). `KEY[0]` es el *reset* (activo-bajo,
  directo a `rst_n`) y `SW[0]` el *enable*.

> **Directorio de proyecto:** todas las rutas relativas de los `.qsf` se resuelven
> desde la **raíz del template** (la carpeta que contiene `src/`, `lib/`, `quartus/`
> y `output_files/`), igual que hace `build_project.tcl`. Abrí el proyecto con esa
> carpeta como directorio de trabajo para que `POST_FLOW_SCRIPT_FILE`,
> `PROJECT_OUTPUT_DIRECTORY` y la compuerta de artefacto funcionen.

## Dos caminos que conviven: GUI y TCL

Hay **dos formas equivalentes** de obtener el proyecto, y coexisten a propósito:

1. **`.qpf`/`.qsf` versionados (GUI):** un estudiante abre
   `digital_design_template.qpf` en Quartus y compila **sin ejecutar nada antes**.
   Este es el camino de "abrir y compilar" que exige la plantilla.
2. **`build_project.tcl` (programático):** `make quartus-project` genera un proyecto
   **equivalente** por línea de comandos (útil en automatización, o para regenerar
   desde cero). Emite los mismos ajustes que captura el `.qsf` de forma declarativa.

Ambos apuntan al mismo `post_flow.tcl`, al mismo `output_files/` y a los mismos
SDC, de modo que la evidencia de procedencia es idéntica venga del camino que
venga.

## Cómo `TARGET_BOARD` selecciona la revisión

`TARGET_BOARD` (ADR-0008) escoge la capa específica de Quartus **sin editar el
RTL**:

- `TARGET_BOARD=de1_soc` (por defecto) → revisión `de1_soc`, `DEVICE 5CSEMA5F31C6`,
  `targets/de1_soc.sdc`, top `de1_soc_top`.
- `TARGET_BOARD=de10_standard` → revisión `de10_standard`, `DEVICE 5CSXFC6D6F31C6`,
  `targets/de10_standard.sdc`, top `de10_standard_top`.

En la GUI se cambia con *Project > Revisions...*; en el flujo TCL/Make con
`make quartus-project TARGET_BOARD=de10_standard`.

## Aviso: pinout de la DE10-Standard SIN VERIFICAR (BL-011)

Las asignaciones de pines de la DE1-SoC siguen la tabla canónica de Terasic
SystemBuilder y son de uso establecido (confirmalas igual contra tu manual antes
de un *bitstream* de producción).

En cambio, el **pinout de la DE10-Standard NO está verificado** contra el manual
oficial de Terasic (BL-011). En `de10_standard.qsf` las asignaciones de pines
están como *placeholders* comentados con marca `# VERIFY`: hay que completarlas
desde el manual **antes** de compilar un *bitstream* de producción. El
dispositivo, el top, el SDC y el post-flow sí son correctos; lo único pendiente
es el mapa físico de pines.
