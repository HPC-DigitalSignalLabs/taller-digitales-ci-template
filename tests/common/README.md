# tests/common

> **Por qué esto NO es una prueba de tu diseño.** Si llegaste aquí buscando las pruebas
> de tu RTL, no están en esta carpeta: están en `tests/<tu_modulo>/` (`test_<diseño>.py`
> para la capa Python y `tb_<diseño>.py` para el testbench de CocoTB). `tests/common/`
> es **infraestructura compartida**: no tiene ningún *design under test*. Su archivo
> `test_transport.py` prueba la **capa de transporte**, no un módulo tuyo; el prefijo
> `test_` solo existe porque PyTest recolecta por ese nombre. Piensa en `common/` como
> la "biblioteca de plomería" que todos los testbenches usan para hablar con el DUT, sea
> en simulación o en la FPGA. Esta aclaración responde a una duda real del supervisor
> (BL-003); el razonamiento completo está en `docs/decisions/ADR-0009-justificacion-capa-transporte.md`.

Esta carpeta no es "otro conjunto de tests" del DUT. Es una **capa de infraestructura**
que define la interfaz común entre:

- los estímulos y *checkers* reutilizables de cada testbench, y
- el canal concreto usado para llegar al dispositivo bajo prueba.

El problema que resuelve es separar **qué** se verifica de **cómo** viajan los datos.
Los testbenches de `tests/<modulo>/` deben hablar contra una API estable de lecturas y
escrituras por palabra; el transporte real puede ser simulación, JTAG UART, UART serie
o el puente HPS-to-FPGA. Así, un mismo *checker* puede correr sin cambios sobre más de
un backend, que es justamente la propiedad de reuso que sustenta OE3.

## Archivos

### `transport.py`

Define la abstracción base:

- `Transport`: interfaz abstracta de lecturas/escrituras por palabra.
- `TransactionLog` y `compare_logs()`: registro y comparación de transacciones para
  medir acuerdo simulación↔hardware.
- `MemoryTransport`: backend de referencia en memoria.

`MemoryTransport` existe para probar la capa en sí misma sin depender de hardware ni de
drivers externos. También sirve como referencia determinista para comparar el
comportamiento de otros transportes.

### `hardware.py`

Implementa backends reales o listos para hardware:

- `StreamTransport`: transporte genérico sobre un flujo de bytes con un protocolo fijo.
- `JtagUartTransport`: backend primario previsto para `altera_jtag_uart`.
- `SerialTransport`: ruta de respaldo por UART.
- `HpsBridgeTransport`: acceso por el puente ligero HPS-to-FPGA.

`StreamTransport` completa escrituras parciales y rechaza respuestas de tipo o
longitud inválidos. Tras un fallo invalida el enlace: se debe restablecer tanto
el canal como el decodificador, porque el protocolo no tiene resincronización
ni ACK de escritura. Una dirección fuera de los 32 bits se rechaza antes de
enviar bytes. Estas garantías se prueban en
`tests/hardware_station/test_wire_boundary.py`, sin acceder a una placa.

**Adaptador JTAG:** `jtag_stream.py` usa `intel-jtag-uart==0.0.3` solo en la
estación nativa (`requirements-hardware.txt`). Abre con `cable_name`,
`device_nr`, `instance_nr`, pero realiza I/O a través de los símbolos nativos:
la API de alto nivel descarta el conteo de escritura y no conserva el conteo
real de lectura. El adaptador usa un buffer mutable, devuelve únicamente los
bytes recibidos y comprueba el resultado de flush. Las firmas C usan `int` y
`unsigned int`, no el tamaño dependiente de plataforma de `long`.

Cada sesión vive en su propio proceso `spawn`; el padre impone un timeout
finito por apertura/operación (2 segundos por defecto) y termina únicamente
ese proceso si la DLL se bloquea. No cambia ni detiene el servicio JTAG.
Después de un fallo no se reintenta la transacción: hay que cerrar el canal,
restablecer UART y decodificador con KEY0, y abrir una nueva sesión. No hay ACK
de escritura; solo las lecturas posteriores pueden confirmar el estado del DUT.
Las pruebas usan dobles y no abren el cable.

El perfil optativo `jtag-counter` integra el canal real con `src/counter.sv`.
`tests/jtag_counter` produce trazas RTL y contiene el mismo generador/checker
que utiliza `tools/jtag_counter_replay.py`. Véase `quartus/README.md` para la
secuencia de preparación y los límites de la evidencia física.

Aunque en esta fase no haya una FPGA conectada, este archivo existe para fijar desde ya
el contrato que usarán los testbenches cuando llegue la validación física en Fase 3.
La idea no es "activar hardware hoy", sino evitar que la llegada del hardware obligue a
reescribir estímulos o *checkers*.

### `test_transport.py`

No prueba un módulo RTL del estudiante. Prueba la **abstracción de transporte**.

Incluye pruebas de API, de *framing* y, sobre todo,
`test_same_checker_runs_over_two_different_transports`, que demuestra que un mismo
*checker* puede ejecutarse sin cambios sobre `MemoryTransport` y `StreamTransport`.
Ese test valida la fontanería: la capa transporta bits igual por cualquier backend.

### `test_reuse_regmapped_design.py`

Sube la demostración de reuso de un bucle trivial a un **checker con forma de diseño**.
Define un bloque **mapeado en registros** parecido a una macro de `lib/` (un acumulador
con registro de control, opcode y banderas), un generador de estímulo sembrado
(`TB_SEED`) y un *checker* escrito **una sola vez** contra la API `Transport` y un modelo
dorado. Ese mismo *checker* corre sin cambios sobre dos transportes distintos
—`MmioTransport` (acceso directo, como el simulador) y `StreamTransport` sobre el
protocolo de trama (como la FPGA por JTAG)— y ambos concuerdan con el modelo. Además
**inyecta un defecto de diseño** y comprueba que el *checker* reusado lo detecta y lo
localiza (escenario 6 del *dry run*, OE4).

Por qué importa: `test_transport.py` prueba que los *bytes* viajan igual; este prueba que
un *checker real* —el que juzga un dispositivo contra su modelo— es de verdad agnóstico
del transporte. Esa es la propiedad que OE3 mide, no un *echo* de direcciones.

## Por qué existe hoy

Esta carpeta existe desde Fase 0 porque la abstracción debe validarse antes de conectar
hardware real. Si se dejara para después, el primer backend físico forzaría decisiones
ad hoc dentro de cada testbench y se perdería el reuso que OE3 exige medir.

En esta fase:

- `MemoryTransport` permite probar la interfaz y los *checkers*.
- `test_transport.py` demuestra el reuso entre transportes distintos.
- `hardware.py` deja preparados los backends de laboratorio para Fase 3.

## Referencia

- `docs/decisions/ADR-0004-canal-host-fpga.md` — decisión **D1** (irrevocable): interfaz
  de transacciones agnóstica del transporte.
- `docs/decisions/ADR-0002-capa-de-verificacion.md` — convención de cuatro archivos;
  `checker.py` no importa `cocotb` ni ningún driver.
- `docs/decisions/ADR-0009-justificacion-capa-transporte.md` — por qué esta carpeta se
  **mantiene y refuerza** en vez de cambiarse o eliminarse (responde a BL-003 y a la
  observación del supervisor sobre `tests/common/`).
