# ADR-0009 — Justificación de la capa de transporte (`tests/common/`): mantener y reforzar

> **Vigencia de alcance — 2026-10-16:** los OEs y métricas de reúso citados en el
> cuerpo corresponden al alcance anterior. Véanse
> ADR-0011 y la
> adenda del anteproyecto:
> JTAG ya no es objetivo ni criterio de éxito independiente. Se conserva la
> capa implementada y su justificación técnica como posible recurso del demo;
> no se exige profundizarla para cerrar un objetivo JTAG separado.

**Fase 1 · S6 · OE3 · Estado: ACEPTADA · 2026-09-21**
**Responde a BL-003 y a la observación del supervisor sobre `template/tests/common/`**

## Contexto

El supervisor planteó, textualmente:

> «No entiendo el propósito de la carpeta `tests` y sus contenidos, particularmente
> `common`, `test_transport`. Verifique la importancia de estos componentes, vea si no
> es mejor cambiarlos. Cuestiónelo, y si pasa las pruebas puede quedarse pero bien
> justificado.»

La observación es legítima y hay que enfrentarla, no esquivarla. `tests/common/`
contiene una **capa de infraestructura de verificación**, no pruebas de un diseño del
estudiante:

- `transport.py` — interfaz abstracta `Transport` (lecturas/escrituras por palabra),
  `TransactionLog`, `compare_logs()` y el backend de referencia `MemoryTransport`.
- `hardware.py` — backends de laboratorio: `StreamTransport` (protocolo de trama fijo),
  `JtagUartTransport`, `SerialTransport`, `HpsBridgeTransport`.
- `test_transport.py` — pruebas de la abstracción, incluida la propiedad de reuso.
- `README.md` — explicación de la carpeta.

Esta capa **materializa la decisión D1 de ADR-0004**: *«el canal se define como una
interfaz de transacciones agnóstica del transporte»*, decisión declarada allí
**irrevocable**. Y es el mecanismo por el que se cumple la convención de cuatro archivos
de ADR-0002, cuya tercera fila —`checker.py` no importa `cocotb` ni ningún driver— es
*literalmente* la métrica de reuso estímulo/checker que **OE3** mide.

## Análisis crítico (ambos lados, con honestidad)

Antes de decidir, se argumentan en serio las objeciones del supervisor.

### Objeción 1 — «Es una abstracción prematura (YAGNI): no hay hardware conectado.»

**A favor de la objeción.** En Fase 1 no hay FPGA en el lazo. Escribir hoy cuatro
backends de hardware (`hardware.py`) que nadie ejecuta es, en apariencia, el manual de
la sobre-ingeniería: código no ejercitado que hay que mantener.

**En contra.** La abstracción no existe *para el hardware de hoy* sino para **impedir que
la llegada del hardware obligue a reescribir estímulos y checkers**. OE3 no mide «tener
un driver JTAG»; mide que *el mismo* checker juzgue la traza del simulador y la de la
FPGA. Esa propiedad hay que **diseñarla desde el inicio**: si cada testbench se escribe
acoplado al simulador y el transporte se «añade después», el reuso se vuelve
irrecuperable sin reescritura —exactamente el escenario que ADR-0002 descarta al rechazar
CocoTB sin convención. La interfaz `Transport` es de ~60 líneas y no es especulativa:
tiene un consumidor real *hoy* (`MemoryTransport` + `StreamTransport` en las pruebas) y
un contrato ya usado por los testbenches. No es «lo necesitaré algún día»; es «lo
necesito para poder medir OE3 y ya lo estoy ejercitando».

Sobre `hardware.py` en concreto: los backends físicos (`JtagUartTransport`,
`HpsBridgeTransport`) **sí** podrían diferirse a Fase 3 sin tocar OE3. Se conservan hoy
porque (a) importan sin Quartus ni placa —está probado— y por tanto no cuestan nada en
CI, y (b) **fijan el contrato de trama** que el RTL del lado FPGA deberá decodificar, que
es material del curso. Son ~200 líneas que congelan una decisión de interfaz, no lógica
activa que haya que depurar. El costo de mantenerlos es bajo y el beneficio —un contrato
estable— es real. Se acepta la mitad de la objeción: `hardware.py` es *preparación*, y se
documenta como tal.

### Objeción 2 — «`test_transport.py` no demuestra el reuso: usa un checker de juguete.»

**Esta es la objeción fuerte, y era cierta.** El checker de `test_transport.py`
(`stimulus_and_check`) escribe `addr*0x1111_1111` y lo lee de vuelta. Una memoria plana
devuelve esos bytes *pase lo que pase*; el test prueba que la **fontanería** es agnóstica
del transporte, pero no que un checker **con forma de diseño** —uno que contrasta un
dispositivo contra un modelo dorado— también lo sea. Un tribunal podía objetar, con
razón, que la demostración no se parecía a un checker real.

**Respuesta (acción tomada).** Se añade `test_reuse_regmapped_design.py`: un bloque
**mapeado en registros** que se parece a una macro de `lib/` (un acumulador mapeado en
memoria con registro de control, registro de opcode y banderas de estado), un generador
de estímulo sembrado y un **checker escrito una sola vez** contra la API `Transport` y el
modelo dorado. Ese mismo checker corre **sin cambios** sobre dos transportes realmente
distintos —`MmioTransport` (acceso directo en proceso, como CocoTB maneja el DUT en
simulación) y `StreamTransport` sobre el protocolo de trama, como la FPGA por JTAG— y
ambos concuerdan con el modelo (`compare_logs() == []`). Además se **inyecta un defecto
de diseño** (SUB cableado como ADD) y se comprueba que el checker reusado lo detecta y lo
localiza. Esto eleva la prueba de un *echo* de direcciones a un checker con forma de
diseño, y responde de frente a «¿la abstracción es real?»: sí, y ahora se demuestra con
un diseño, no con un bucle trivial.

### Objeción 3 — «El nombre `test_transport` bajo `tests/` confunde: parece la prueba de mi diseño.»

**A favor.** Es exactamente la confusión que el supervisor reportó, y es predecible: un
estudiante ve `tests/…/test_*.py` y asume «pruebas de mi RTL». El ruido cognitivo es
real.

**En contra de mover la carpeta.** La alternativa —sacar la infraestructura a un paquete
`veriflib/` o `tblib/`— tiene un costo de *churn* alto: rompe rutas de importación en
todos los testbenches (`sys.path`, la convención de `tests/<modulo>/`), obliga a tocar
`Makefile`, `pytest.ini` y READMEs que **otros dueños controlan**, y desalinea con la
estructura que el curso ya observa (`docs/fase0/observacion-repos-curso.md`). El
beneficio —menos confusión— se obtiene más barato con **documentación y nombres
afilados** que con una reubicación. La confusión no viene de la ubicación sino de la
ausencia de una señal clara de que esto *no* es un test de DUT.

**Resolución.** Se **mantiene la ubicación** `tests/common/` y se ataca la confusión en
su raíz: (1) el `README.md` gana un párrafo explícito «Por qué esto NO es una prueba de
tu diseño»; (2) los nombres ya distinguen intención —los testbenches de CocoTB son
`tb_<diseño>.py` y las pruebas PyTest de un módulo son `tests/<modulo>/test_<diseño>.py`,
mientras que `tests/common/` es infraestructura compartida sin DUT—; (3) el test de
convención (`tests/convention/test_testbench_convention.py`) ya vigila mecánicamente esa
separación de nombres. `common` es el nombre canónico para «código compartido entre
suites» y no promete ser un DUT.

## Decisión

**MANTENER y REFORZAR `tests/common/`.** No se cambia la API pública de `Transport`
(rompería `test_transport.py` y los testbenches de `tests/<modulo>/`). Se refuerza así:

1. Se añade `tests/common/test_reuse_regmapped_design.py` —la demostración con forma de
   diseño descrita arriba— que sube la prueba de reuso de un bucle trivial a un checker
   contra modelo dorado, con defecto inyectado detectado.
2. Se afina `tests/common/README.md` con la justificación y con el párrafo dirigido al
   lector confundido.
3. Se registra este razonamiento como ADR para trazabilidad de BL-003.

El veredicto sobre la evidencia es **KEEP-BUT-STRENGTHEN**, no por inercia: la evidencia
concreta es que (a) OE3 mide precisamente esta propiedad, (b) ADR-0004 D1 la declara
irrevocable, (c) las 20 pruebas de `tests/common/` pasan en verde, y (d) la única
debilidad real —un checker de juguete— queda subsanada con una demostración con forma de
diseño. Si la evidencia dijera lo contrario —si el reuso no se pudiera demostrar sobre un
segundo transporte— la conclusión honesta sería REMOVE; no es el caso.

## Objeción más fuerte y respuesta

*«Aun reforzado, siguen siendo pruebas de infraestructura que no prueban ningún RTL. En
un TFG de diseño digital, ¿no es alcance inventado?»*

Concedido que no prueba RTL; **por diseño no debe hacerlo**. La infraestructura de
verificación reutilizable *es* una contribución central de este TFG: el título es
«Infraestructura híbrida de CI/CD para diseño y verificación», y OE3 pide explícitamente
«un canal de comunicación para enlazar los estímulos con el dispositivo físico» y mide su
reuso. Sin esta capa, cada testbench reinventaría el acceso al DUT y el reuso
simulación→hardware sería inmedible. La respuesta de fondo: esta capa es el *habilitador*
de las métricas de OE3 (acuerdo sim/hardware 100 %, detección de divergencia inyectada,
≥X% de secuencias reutilizadas), no un adorno. Y su costo está acotado: API estable de
~60 líneas, backends que importan sin hardware, cero flakes.

## Alternativas descartadas

| Alternativa | Razón |
|---|---|
| **Eliminar `tests/common/`** | Haría inmedible el reuso de OE3 y violaría ADR-0004 D1 (irrevocable). Cada testbench acoplaría el acceso al DUT; ADR-0002 ya descarta ese camino |
| **Mover a `veriflib/` o `tblib/`** | *Churn* alto (importaciones, `Makefile`, `pytest.ini`, READMEs de otros dueños) por un beneficio —menos confusión— que se logra más barato con docs y nombres. Desalinea con la estructura observada del curso |
| **Diferir `hardware.py` completo a Fase 3** | Perdería el contrato de trama estable que el RTL del lado FPGA necesita como referencia; los backends ya importan sin costo en CI. Se acepta parcialmente: se documenta como *preparación*, no lógica activa |
| **Dejar solo `test_transport.py` (checker de juguete)** | No convence a un tribunal de que un checker *con forma de diseño* sea agnóstico del transporte. Subsanado con `test_reuse_regmapped_design.py` |
| **Renombrar `test_transport.py`** | PyTest recolecta por prefijo `test_`; el nombre es correcto para una prueba PyTest. La confusión se resuelve documentando que `tests/common/` es infraestructura, no un DUT |

## Impacto en los objetivos

**Ninguno negativo sobre OE1–OE4; refuerza OE3.** La API pública de `Transport` no
cambia, así que ningún testbench se rompe. La nueva prueba fortalece la evidencia de la
métrica de reuso de OE3 y aporta un caso más de detección de divergencia inyectada
(escenario 6 del *dry run*, OE4). Se mantiene la reproducibilidad 1:1 y `flaky rate ≈ 0`
(OE2): la prueba es pura Python, sembrada con `TB_SEED`, y corre en el contenedor de
verificación sin Quartus ni placa.

## Verificación

```
cd template
python -m pytest tests/common -q      # 20 passed (16 previas + 4 nuevas)
```

## Referencias

- ADR-0004 (D1) — canal host↔FPGA agnóstico del transporte (irrevocable).
- ADR-0002 — capa de verificación; convención de cuatro archivos; `checker.py` agnóstico.
- BL-003 — README de `tests/common/` explicando la capa de transporte.
- `tests/common/README.md`, `tests/common/test_reuse_regmapped_design.py`,
  `tests/convention/test_testbench_convention.py`.
