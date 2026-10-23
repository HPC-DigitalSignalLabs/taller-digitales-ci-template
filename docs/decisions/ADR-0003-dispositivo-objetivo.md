# ADR-0003 — Dispositivo objetivo: DE1-SoC

**Fase 0 · S2 · OE1 · Estado: ACEPTADA · 2026-08-11 · Sustituye la plataforma del anteproyecto**

## Contexto

El anteproyecto (redacción original) fija como plataforma la **DE10-Lite** (Intel MAX 10,
`10M50DAF484C7G`). Los dos repositorios del curso CE3201 (`ce3201` y
`ce3201_s2_2026_template`) apuntan en cambio a la **DE1-SoC** (Intel Cyclone V SoC,
`5CSEMA5F31C6`). Aparecieron así tres tarjetas nombradas en tres fuentes distintas; ver
`docs/fase0/observacion-repos-curso.md` §3 para el detalle de la discrepancia y su
resolución en Fase 0.

## Decisión

**La plataforma única del TFG es la DE1-SoC (Intel Cyclone V SoC, `5CSEMA5F31C6`).**

El anteproyecto (`docs/anteproyecto.md`) se corrige en consecuencia: se abandona la
DE10-Lite (MAX 10) de la redacción original, y se alinea con la tarjeta de referencia que
ya usan ambos repositorios del curso CE3201.

## Consecuencias — qué cambia y qué no

### No cambia (importante)

**Ningún objetivo específico se ve afectado.** OE1–OE4 están redactados a nivel de
infraestructura y no nombran dispositivo.

Tampoco cambia la arquitectura por dominio de software, ni el pipeline *gatekeeper*, ni
el plano HPC, ni la capa de verificación (Verilator + CocoTB + PyTest).

### Cambia

1. **El canal host↔FPGA.** Consecuencia mayor; se trata por separado en **ADR-0004**.
   Resumen: el USB-Blaster II integrado **no** es FTDI, por lo que `pyftdi` queda
   descartado, y el HPS abre un canal mejor que JTAG puro.
2. **Módulo didáctico 1 (mapeo RTL→recursos lógicos).** MAX 10 usa *logic elements* con
   LUT de 4 entradas; Cyclone V usa **ALM con LUT fracturable de 6 entradas**. El
   fenómeno que se enseña cambia de forma y el material debe reescribirse sobre ALM,
   fracturado de LUT y cadenas de acarreo de Cyclone V.
3. **Recursos de tarjeta.** La DE1-SoC trae 4 pulsadores (`KEY[3:0]`), 10 interruptores
   (`SW[9:0]`), 10 LED rojos (`LEDR[9:0]`), 6 displays de 7 segmentos (`HEX0`–`HEX5`),
   salida VGA, códec de audio de 24 bits, decodificador de TV, PS/2, receptor IR,
   Ethernet 10/100, USB 2.0 OTG, ranura micro SD y cabezales de expansión GPIO de 2×40
   pines. A diferencia de la DE10-Nano (evaluada y descartada en esta misma decisión),
   no trae HDMI, ADC embebido ni cabezal Arduino — los módulos de referencia no deben
   depender de esos periféricos.
4. **Recursos del FPGA.** El dispositivo `5CSEMA5F31C6` tiene **32 070 ALM** (frente a
   ~50 000 LE del MAX 10 de la redacción original): sigue habiendo holgura amplia para
   el núcleo RV32I mínimo, aunque menos que la DE10-Nano evaluada (`5CSEBA6U23I7`,
   41 910 ALM). Sin impacto negativo sobre el alcance de Fase 1.
5. **SDC.** Se mantiene la convención (`create_clock`, `derive_pll_clocks`,
   `derive_clock_uncertainty`), pero los relojes y las restricciones de E/S son de
   Cyclone V y usan los nombres de puerto propios de la DE1-SoC (`CLOCK_50`, `SW[*]`,
   `KEY[*]`, `LEDR[*]`), no los de la DE10-Nano (`FPGA_CLK1_50`, `LED[*]`). El reloj de
   fabric de la DE1-SoC es de 50 MHz.

## Objeción más fuerte y respuesta

*«Cambiar de plataforma en S2 desperdicia el trabajo de Fase 0 y añade riesgo.»*

El trabajo de Fase 0 realizado hasta ahora es observación de repos, ADRs y la compuerta
de artefacto: todo independiente del dispositivo. Lo desperdiciado es cero. El riesgo
real es el inverso: descubrir el conflicto en Fase 3, con la biblioteca de macros y los
SDC ya escritos contra MAX 10 o contra una tarjeta distinta a la que finalmente hay
disponible. Detectarlo en S2 es el mejor momento posible.

Un segundo argumento a favor: la DE1-SoC **es** la tarjeta de referencia de ambos
repositorios del curso CE3201 (`ce3201` y `ce3201_s2_2026_template`), misma familia
Cyclone V que la redacción original ya asumía en espíritu (aunque el anteproyecto
nombrara MAX 10). Esto es cohesión buscada con el curso, no una restricción impuesta.

---

**Nota de revisión (S4):** esta ADR fijó originalmente DE1-SoC como plataforma en S2,
pero el hardware con acceso físico confirmado durante parte de Fase 0 fue una DE10-Nano
(ver `docs/fase0/observacion-repos-curso.md` §3, dejado como registro histórico, no
retroactivo). En S4 se confirma que el dispositivo real de trabajo es la **DE1-SoC**, y
esta ADR, `docs/anteproyecto.md`, `.github/workflows/gatekeeper.yml` y el resto de
artefactos vivos del repositorio (SDC, README, testbenches, agente SANDRO) se
actualizaron en consecuencia. Los documentos bajo `docs/fase0/` y `docs/historico/` que
narran la evaluación de la DE10-Nano se conservan sin modificar como registro fechado de
esa etapa, y no deben leerse como el estado actual del proyecto.

**Nota de revisión (2026-09-08):** la fijación de la DE1-SoC como plataforma única queda
superada por `docs/decisions/ADR-0008-target-configurable.md`, que parametriza la
plantilla por `TARGET_BOARD` (`de1_soc` o `de10_standard`) sin borrar esta decisión como
registro histórico.
