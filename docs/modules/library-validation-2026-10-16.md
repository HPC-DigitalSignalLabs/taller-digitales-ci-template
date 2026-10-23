# Biblioteca E1 — contratos y validación del 16 de octubre de 2026

## Alcance y trazabilidad

Fuente oficial: anteproyecto, adenda vigente §§V2–V4.
OE2 vigente: **Construir una biblioteca de módulos digitales reutilizables que
sirva de base para desarrollar la infraestructura de pruebas.**

| Familia requerida | Referencia | Contrato y pruebas |
|---|---|---|
| Sincronizadores | `sync_2ff` existente | [Contrato digital de dos etapas](sync_2ff.md); `tests/sync_2ff/` |
| FSM | `control_fsm` | [start/finish/busy/done](control_fsm.md); `tests/control_fsm/` |
| Registros | `enabled_register` | [Reset, carga y retención](enabled_register.md); `tests/enabled_register/` |
| I/O | `bin2bcd`, `bcd_to_7seg` | [BCD sin truncamiento](bin2bcd.md), [decimal/polaridad/inválidos](bcd_to_7seg.md); ambas carpetas de pruebas homónimas |

Se conservan `counter`, `skid_buffer`, `alu` y `edge_detector` como
auxiliares/heredados. `sync_2ff` es simultáneamente previo y reutilizado
para la nueva familia; no hay duplicación de su RTL. Sincronizar un botón
no elimina sus rebotes. No se agregaron debouncer, MMIO, demo ni JTAG.

Enfoque estándar: módulos pequeños de un propósito, referencia Python
independiente y separación `model/stimulus/checker/tb`. El checker no importa
simulador ni transporte. No se añadieron frameworks.

## Comandos reproducibles de biblioteca

Desde `template/`, con el entorno de verificación preparado:

```sh
make designs
make library-test
make library-regression
make synth-all
make netlist-all
python -m pytest tests/integration/test_library_surface.py \
  tests/integration/test_sim_recipe.py tests/synthesis tests/convention -q
```

`library-regression` exige Verilator **e** Icarus antes de ejecutar PyTest;
no obtiene un PASS por omisión de ambos simuladores. Las pruebas PyTest
individuales son portables y saltan una herramienta ausente: para evidencia
de RTL hay que revisar los skips, no solo el exit code.
El target elimina primero `junit-library.xml` y `library.html`: si falta
un simulador o PyTest falla antes de generar evidencia, un reporte anterior
no puede hacerse pasar por la ejecución actual. La prueba de receta
`tests/integration/test_library_regression_recipe.py` cubre esos casos sin
depender de herramientas HDL instaladas. El gatekeeper debe respetar el
fallo de `make` y consumir el XML explícitamente:

```sh
make library-regression &&
python tools/human_report.py --junit results/junit-library.xml --require-pass
```

La integración conserva rutas heredadas y aísla cada top de biblioteca de
RTL de estación/placa. WIDTH llega al elaborador en ambos simuladores para
registro y conversor; nunca se aplica a FSM ni decoder sin ese parámetro.

**Precaución con GNU Make:** `make -n synth-all` no es una inspección sin
efectos: GNU Make ejecuta la línea de receta que contiene `$(MAKE)`, incluidas
la limpieza y generación del reporte que la acompañan. La prueba de cobertura
de agregados inspecciona el texto de la receta, no la ejecuta con `-n`.
Las corridas reales de esta validación usan `RESULTS_DIR` aislado.

## Entorno observado

El host no tenía Verilator, Icarus, Yosys ni CocoTB disponibles inicialmente.
Se reutilizó `.venv`; se instalaron CocoTB 1.9.2 y pytest-html 4.1.1 tras
comprobar la ausencia. Se extrajeron paquetes del repositorio Arch Linux
en `/tmp/tfg-rtl-tools`, sin sudo y sin modificar el contenedor ni el sistema.

| Herramienta | Versión local observada |
|---|---|
| Python | 3.14.8 |
| PyTest | 9.1.1 en las primeras corridas; 8.3.4 en `library-test` posterior |
| CocoTB | 1.9.2 |
| pytest-html | 4.1.1 |
| Verilator | 5.052 |
| Icarus | 13.0 |
| Yosys | 0.66 |

No se modificaron pins del repositorio. Las versiones HDL son diferentes
de las fijadas en el contenedor (5.028, 12.0 y 0.40 respectivamente).
**Estos resultados locales no acreditan reproducibilidad 1:1** local/Kabré/runner
ni sustituyen la corrida posterior de la imagen fijada.

El paquete Yosys esperaba ABC en `/usr/bin/abc`. Para poder ejecutarlo
reubicado se utilizó un shim temporal, fuera del repositorio, que configura
`scratchpad -set abc9.exe /tmp/tfg-rtl-tools/usr/bin/abc` y `abc.exe` con la
misma ruta. `LD_LIBRARY_PATH` apunta al `usr/lib` temporal.
No se alteró la lógica de síntesis ni se relajó `check -assert`.

## Evidencia ejecutada

Resultados y HTML/JUnit locales: `/tmp/tfg-rtl-results/` (temporales,
no artefactos versionados). La ejecución integral posterior genera evidencia
en `template/results/`, también excluida de Git.

| Comprobación | Resultado observado |
|---|---|
| Nuevos módulos: `pytest tests/control_fsm tests/enabled_register tests/bin2bcd tests/bcd_to_7seg` | 147 PASS, cero skips; incluye 24 simulaciones (12 por simulador), 574.53 s |
| Cinco diseños preservados: `pytest tests/counter tests/skid_buffer tests/alu tests/sync_2ff tests/edge_detector` | 71 PASS, cero skips; incluye 19 simulaciones, 507.49 s |
| Reejecución final FSM/registro con estabilidad entre flancos | 10 configuraciones únicas PASS; XML e informe en `final-sequential/` |
| `make library-test RESULTS_DIR=/tmp/tfg-rtl-results/library-unit` | 175 PASS, 43 simulaciones excluidas expresamente |
| Integración de superficie/receta + síntesis + convención | 101 PASS, incluyendo 8 mapeos nuevos y 3 rechazos de parámetros inválidos |
| Promesas seleccionadas de biblioteca/Makefile | 17 PASS, 46 pruebas ajenas no seleccionadas |
| Exportación de referencia CI | 1 PASS |
| `make synth-all` | 16 variantes producen JSON y veredicto estructural PASS |
| `make netlist-all` | 10 diagramas DOT/SVG, incluidas ambas polaridades del detector heredado |
| Revisión final superficie/convención | 66 PASS; `git diff --check` del alcance sin errores |
| Instantánea del commit FSM, sin registro ni I/O nuevos | 53 PASS, incluidas ambas simulaciones de FSM |
| Instantánea del commit de registro, sin I/O nuevo | 66 PASS, incluidas las ocho configuraciones RTL de registro |
| Ejecución integral final de `make library-regression` | 218 PASS, cero omisiones, 1036.28 s; `results/junit-library.xml` |
| Validación explícita del XML final con `human_report.py --require-pass` | PASS; `results/report-library.md` |
| `make smoke` final | 739 PASS, 56 excluidas por marcador, 4.17 s; `results/junit-smoke.xml` |

La suite nueva y la heredada suman **218 casos distintos: 175 pruebas puras
y 43 configuraciones de simulación**. La fila de `library-test` y la
reejecución secuencial y la regresión integral son repeticiones deliberadas, no pruebas adicionales
que deban sumarse para inflar la cobertura. Los tiempos corresponden al host
observado, no a una medición CI controlada. Las regresiones iniciales nueva
y heredada corrieron concurrentemente; la integral final recorre los nueve
diseños en una sola invocación, con PyTest 8.3.4. Su código queda registrado
hasta `8705367`; no es una ejecución remota de Actions ni un resultado de
la imagen fijada.

La última revisión añadió comprobaciones de que FSM/registro no cambian
por variaciones de entrada entre flancos (salvo aserción asíncrona de reset).
Se reutilizaron los builds HDL sin cambiar su RTL y se ejecutaron nuevamente
los drivers finales en las diez configuraciones. Un primer listado incluyó
alias simbólicos `current` de PyTest y contó 13 ejecuciones; la validación
posterior deduplicó la evidencia por los diez XML y todos pasaron.
El informe final usa `--junit` explícito por cada XML.

Los 16 mapeos del agregado: contador w8; ALU w8/w32;
skid buffer w1/w8/w32; sincronizador; detector r0/r1; FSM;
registro w1/w8/w32; bin2bcd w8/w32; decoder BCD.
La prueba de síntesis añade `bin2bcd WIDTH=1` para verificar su optimización
a conexiones/constantes y preservación del ancho de salida.

## Límites y riesgos pendientes

- No hay evidencia nueva en FPGA, Quartus, STA, SDC del sistema ni JTAG.
  El reloj de 20 ns en CocoTB no es una medición de timing físico.
- No hay prueba analógica de metastabilidad ni MTBF; tampoco antirrebote.
- Double-dabble combinacional tiene coste creciente con WIDTH. Validar
  timing en Quartus antes de elegirlo para un camino del demo.
- La tabla BCD→7 segmentos cubre exhaustivamente entradas binarias y
  polaridades; no acredita comportamiento físico de X/Z ni transitorios.
- Tests negativos de checkers prueban detección de divergencias concretas,
  **no** constituyen una campaña RTL de mutación ni un kill rate medido.
- El ABC del paquete temporal emitió diagnóstico interno de optimización
  (`Transformation has failed`) en una etapa, continuó, y el flujo final
  pasó `check -assert` y el validador de netlist. Por ello el resultado se
  reporta solo como elaboración/mapeo estructural, no equivalencia formal.
- Las corridas iniciales corresponden al árbol de trabajo antes de agruparlo
  en commits. Las instantáneas intermedias se exportaron del índice y se
  probaron antes de registrar las features FSM (`201d819`) y registro
  (`994d8b2`). Las repeticiones no se suman a los 218 casos distintos.
  No se hizo push; queda pendiente ejecutar el gatekeeper remoto y conservar
  su evidencia asociada al SHA definitivo, con la imagen fijada.

**Objeción:** una biblioteca en verde no demuestra el pipeline end-to-end.
**Respuesta:** esta entrega cierra contratos y pruebas de E1/OE2 dentro del
alcance autorizado. El *pipeline gatekeeper*, su operación con PRs y el demo
son trabajos coordinados aparte; no se declara cerrado OE3.
