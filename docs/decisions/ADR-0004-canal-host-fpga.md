# ADR-0004 — Canal host↔FPGA: JTAG primario, transporte conmutable

> **Vigencia de alcance — 2026-10-16:** el OE3 citado abajo pertenece a la
> formulación anterior. ADR-0011 y la
> adenda del anteproyecto trasladan
> JTAG como objetivo independiente a recomendaciones; puede seguir siendo un
> transporte del demo. Esta ADR conserva la investigación técnica y la historia,
> no impone un criterio autónomo JTAG al alcance vigente. La reunión reportó
> un problema de simulación de VJTAG de causa no confirmada; su BB/adaptador y
> opciones A/B siguen pendientes, sin alterar aquí las guías ni la implementación.

**Fase 0 · S2 · OE1/OE3 · Estado: ACEPTADA · 2026-08-11**
**Revisión 2 — corrige una lectura errónea de la restricción de software abierto en la revisión 1**

## Contexto

OE3 exige *"un canal de comunicación para enlazar los estímulos de verificación con el
dispositivo físico"*. El anteproyecto lo implementa como un **driver JTAG en Python**.

Con el cambio a DE1-SoC (ADR-0003) se investigó el supuesto pendiente *"el USB-Blaster
es controlable desde Python en Linux con software abierto"*.

### Corrección de la revisión 1

La revisión 1 de este ADR descartó JTAG como vía primaria porque el USB-Blaster II
requiere el firmware `blaster_6810.hex`, que se obtiene de una instalación de Quartus.
**Ese razonamiento era incorrecto** y se registra aquí para trazabilidad.

El error fue de alcance: la restricción *"solo software abierto"* del anteproyecto aplica
a **Kabré**, no al puesto de laboratorio. La arquitectura define **tres** dominios, y la
validación en hardware es el tercero:

| Dominio | Dónde corre | Software |
|---|---|---|
| 1 — Verificación funcional abierta | Contenedor: máquina del estudiante, Kabré, *runners* | Solo abierto. Aquí sí aplica una regla obligatoria |
| 2 — EDA propietario | Puesto de laboratorio, nativo | Quartus, Questa. Licencia *node-locked* |
| 3 — **Validación en hardware** | Puesto de laboratorio, con la FPGA conectada | **Puede usar Quartus** |

La FPGA está físicamente conectada a un puesto de laboratorio. El dominio 3 nunca iba a
correr en Kabré, así que una dependencia de Quartus en el canal **no viola ninguna
restricción**. Los estudiantes de CE3201 tienen Quartus instalado y licenciado.

El principio operativo correcto no es *"solo abierto"* sino **"tan abierto como sea
razonable"**: se prefiere la herramienta abierta cuando funciona, y se acepta Quartus en
el dominio 3 cuando aporta valor.

## Hallazgos que se mantienen (hechos de hardware, independientes de licencias)

| Hallazgo | Estado | Fuente |
|---|---|---|
| La DE1-SoC lleva **USB-Blaster II**: Cypress EZ-USB FX2LP `CY7C68013A` + MAX II | CONFIRMADO | Esquemático Terasic; `openFPGALoader/doc/cable.yml` |
| VID/PID `09fb:6810` (sin firmware) → `09fb:6010` (operativo) | CONFIRMADO | `openFPGALoader/src/cable.hpp`; `openocd/tcl/interface/altera-usb-blaster2.cfg` |
| **`pyftdi` no puede manejar el cable integrado.** FX2LP no es FTDI: es un hecho físico, no de licenciamiento. Descartado de forma definitiva | CONFIRMADO | Documentación pyftdi |
| **UrJTAG tampoco**: su driver `UsbBlaster` cubre PID `6001/6002/6003` (Blaster I), no `6810/6010` | CONFIRMADO | `urjtag/src/tap/cable/usbblaster.c` |
| openFPGALoader soporta la placa (`-b de1Soc`) y el cable; necesita `blaster_6810.hex` de Quartus — **ahora aceptable** | CONFIRMADO | `openFPGALoader/src/usbBlaster.cpp` |
| openFPGALoader **no expone `irscan`/`drscan` interactivos**: es programador, no transporte de datos | CONFIRMADO | README, `usbBlaster.hpp` |
| **Virtual JTAG (`sld_virtual_jtag`) está soportado en Cyclone V** | CONFIRMADO | Intel Virtual JTAG IP User Guide |
| Alcanzar un nodo VJTAG exige enumerar el **SLD hub** (USER1 → enumerar → VIR → USER0 → VDR) | CONFIRMADO | `openocd/src/target/openrisc/or1k_tap_vjtag.c` |
| `intel_jtag_uart` (Python, Linux, funciona) envuelve `libjtag_atlantic.so` de Quartus — **ahora aceptable** | CONFIRMADO | `tomverbeure/intel_jtag_uart` |
| El HPS accede al fabric por el puente ligero HPS-to-FPGA en `0xFF200000`, desde Python vía `/dev/mem` o UIO | CONFIRMADO | Cyclone V HPS TRM; ejemplo `bleviet/cvsoc` |

## Decisión

### D1 — El canal se define como una **interfaz de transacciones agnóstica del transporte**

Decisión estructural, irrevocable, e independiente de qué transporte gane. Los estímulos
y los *checkers* de CocoTB hablan contra una interfaz Python única:

```
        estímulos + checker  (escritos una vez)
                  │
        interfaz de transacciones
       ┌──────────┼──────────┬─────────┐
   simulador   JTAG UART   VJTAG      HPS
   (CocoTB)   (primario)  (didáctico) (adicional)
```

Materializa el estándar del proyecto —*el checker se mantiene agnóstico del transporte
para que el mismo checker corra contra el simulador y contra la FPGA*— y es lo que OE3
mide como reuso. Añadir un transporte no toca ningún testbench.

### D2 — **JTAG es el canal primario**, por valor pedagógico y de industria

Se conserva JTAG como vía principal. Es la práctica estándar de la industria para
depuración *in-system*, es lo que un estudiante de diseño digital debe conocer al salir
del curso, y conecta directamente con SignalTap y el Timing Analyzer que el curso ya usa.
El valor educativo es parte del objetivo del TFG, no un accesorio.

Se implementa en dos niveles, deliberadamente:

| Nivel | IP en el diseño | Host | Rol |
|---|---|---|---|
| **A — JTAG UART** | `altera_jtag_uart` | `intel_jtag_uart` (Python) sobre `libjtag_atlantic.so` | **Transporte primario de datos.** Flujo bidireccional; camino más corto a "estímulos reutilizados contra silicio" |
| **B — Virtual JTAG** | `sld_virtual_jtag` | `quartus_stp` TCL desde Python, u OpenOCD si resulta viable | **Nivel didáctico.** Expone VIR/VDR y el SLD hub: el estudiante ve la máquina de estados TAP real, no una abstracción |

Carga del *bitstream*: **openFPGALoader** (abierto) usando el firmware del Quartus
instalado. Se prefiere sobre `quartus_pgm` por ser la opción abierta que sí funciona.

### D3 — El **HPS** se añade como transporte adicional

La DE1-SoC tiene un Cortex-A9 dual con Linux que la DE10-Lite no tenía. Se implementa
como *backend* adicional, no como sustituto:

- Camino de alto ancho de banda para regresiones largas contra hardware.
- No necesita nada propietario en tiempo de ejecución.
- Es un segundo transporte real y, por tanto, **la mejor prueba de que D1 funciona**: si
  el mismo checker pasa sobre JTAG y sobre HPS, la agnosticidad queda demostrada
  empíricamente, no afirmada.

Prioridad posterior a JTAG. Si el cronograma aprieta, se difiere sin impacto en OE3.

## Objeción más fuerte y respuesta

*«Tres transportes es dispersión en un cronograma de un desarrollador y 16 semanas.»*

Concedido en parte, y por eso están priorizados y no son simultáneos: JTAG UART es el
único que debe existir para satisfacer OE3. VJTAG y HPS son incrementos.

La respuesta de fondo es que el costo marginal de un segundo transporte, **dado D1**, es
un *backend* de unas 100 líneas. El costo alto sería escribir tres veces los estímulos y
los *checkers*, que es exactamente lo que D1 impide. Y sin un segundo transporte, la
agnosticidad de la interfaz es una afirmación no verificada.

## Impacto en los objetivos

**Ninguno sobre OE1–OE4.** OE3 dice *"un canal de comunicación"*, no nombra transporte.
Las métricas (acuerdo sim/hardware 100 %, detección de divergencia inyectada, ≥X% de
secuencias reutilizadas) son independientes del transporte.

Cambia en el `.tex`: `pyftdi` desaparece como opción (hecho de hardware), entran JTAG UART
y Virtual JTAG como las dos vías concretas, y se añade el HPS.

## Experimentos de Fase 0 (plazo acotado: 2 semanas, criterio de corte explícito)

| # | Experimento | Criterio de éxito | Si falla |
|---|---|---|---|
| E1 | `openFPGALoader -b de1Soc` carga un *bitstream* de parpadeo de LED usando el firmware del Quartus local | LED parpadea | Usar `quartus_pgm`; sin impacto en OE |
| E2 | Instanciar `altera_jtag_uart`; `intel_jtag_uart` desde Python hace *loopback* de 10 000 palabras con secuencia y CRC | 0 errores | Ir a E3 |
| E3 | Instanciar `sld_virtual_jtag`; leer un registro virtual de 32 bits desde Python vía `quartus_stp` | Valor correcto | Ir a E4 |
| E4 | *Backend* UART sobre cabezal GPIO (PySerial), *loopback* con CRC | 0 errores | Escalar: OE3 degrada a SignalTap + comparación de trazas |
| E5 | (No bloqueante, Fase 3) Linux del HPS por SSH; Python mapea `0xFF200000` y verifica 10 000 transacciones | 0 errores | Se difiere el transporte HPS; sin impacto en OE3 |

## Alternativas descartadas

| Alternativa | Razón |
|---|---|
| `pyftdi` contra el cable integrado | **Imposible físicamente**: el puente es FX2LP, no FTDI |
| UrJTAG contra el cable integrado | Su driver no cubre los PID del Blaster II |
| openFPGALoader como transporte de datos | Es programador; sin `irscan`/`drscan` interactivos. Sí se adopta para cargar el *bitstream* |
| OpenOCD + FTDI externo + VJTAG como vía primaria | Exige implementar el SLD hub y acceso físico al TAP sin contienda con el Blaster integrado. Queda por explorar dentro del nivel B |
| HPS como transporte primario | Descarta el valor pedagógico de JTAG, que es parte del objetivo del curso |

---

**Nota de revisión (2026-09-08):** ADR-0008 vuelve configurable el *target* físico entre
DE1-SoC y DE10-Standard. La lógica de este ADR se mantiene: ambas placas son Cyclone V
SoC con HPS Cortex-A9 y USB-Blaster II integrado, por lo que JTAG, JTAG UART y el puente
HPS↔FPGA siguen siendo canales razonables. Lo que pasa a depender de `TARGET_BOARD` es el
pinout, la configuración exacta del proyecto Quartus y la automatización concreta del
puesto/runner de laboratorio.
