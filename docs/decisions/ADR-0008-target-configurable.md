# ADR-0008: Target de FPGA configurable

**Estado:** ACEPTADA · SUPERSEDE ADR-0003  
**Fecha:** 2026-09-08  
**Decisores:** estudiante y contraparte académica  
**Afecta:** OE1, OE2, OE3 y OE4

## Contexto

ADR-0003 fijó la DE1-SoC como plataforma única del TFG para resolver una
discrepancia real entre el anteproyecto, los repositorios del curso y el
hardware observado en Fase 0. Esa decisión estabilizó el trabajo inicial, pero
dejó el repositorio acoplado a una sola tarjeta en varios puntos vivos:
`README.md`, `template/README.md`, `template/quartus/de1_soc.sdc` y el pipeline
de CI documentan la DE1-SoC como si fuera el único destino posible.

La contraparte académica solicitó templetizar el repositorio **como proyecto de
Quartus**, de modo que distintos cursos o profesores puedan seleccionar el
*target* físico disponible sin hacer *fork* ni reescribir RTL. El caso concreto
aprobado para esta primera parametrización es:

- `de1_soc` → Terasic **DE1-SoC**, Intel Cyclone V SoC `5CSEMA5F31C6`.
- `de10_standard` → Terasic **DE10-Standard**, Intel Cyclone V SX SoC
  `5CSXFC6D6F31C6`.

La investigación pública disponible de Terasic muestra que ambas tarjetas
comparten la base que interesa al TFG:

- SoC Cyclone V con **HPS dual-core Cortex-A9**.
- **USB-Blaster II** integrado para configuración/depuración.
- reloj principal de **50 MHz** en el dominio FPGA.
- 10 *switches*, 10 LEDs rojos, seis *seven-segment displays* y periféricos de
  laboratorio comparables para demostraciones introductorias.

También existen diferencias reales que hacen incorrecto tratar una como alias de
la otra:

- **Dispositivo** distinto: `5CSEMA5F31C6` frente a `5CSXFC6D6F31C6`.
- **Capacidad** distinta: la DE10-Standard usa un Cyclone V SX más grande
  (Terasic publica 41 509 ALM / 110K LE frente a 32 070 ALM / 85K LE en la
  DE1-SoC).
- **Expansión** distinta: DE1-SoC expone dos cabezales GPIO de 2×40; la
  DE10-Standard expone un cabezal de 40 pines y además un **HSMC**.
- El **pinout físico** y la distribución exacta de señales de panel no deben
  asumirse equivalentes sin verificar el manual oficial de cada placa.

Por tanto, mantener una “plataforma única” en la plantilla ya no maximiza el
objetivo pedagógico: lo importante no es fijar una tarjeta, sino fijar un flujo
reproducible sobre una familia realista de hardware del curso.

## Decisión

1. Se introduce la variable **`TARGET_BOARD`** con valores permitidos
   **`de1_soc`** y **`de10_standard`**.
2. `TARGET_BOARD` parametriza la capa específica de Quartus y del *pipeline*:
   - el **SDC** cargado por el proyecto Quartus;
   - el **dispositivo/familia** del proyecto (`DEVICE`, `FAMILY` y metadatos
     equivalentes del `.qsf` o del Tcl que lo genera);
   - la selección de archivos de apoyo del flujo físico y la configuración del
     job de hardware/CI cuando ese job esté habilitado.
3. La selección del *target* se realiza **sin editar el RTL mantenido**. La
   variación por tarjeta vive en la capa de proyecto Quartus, *wrappers* de
   placa si hicieran falta, restricciones y automatización.
4. Lo que **puede cambiar por tarjeta** queda explícitamente aislado:
   - nombres y ancho real de puertos top-level conectados a la placa
     (`CLOCK_50`, `KEY[*]`, `SW[*]`, `LEDR[*]`, GPIO/HSMC, etc.);
   - asignaciones físicas de pines e I/O standard;
   - `DEVICE` y cualquier ajuste propio del *fitter* o del Timing Analyzer;
   - restricciones temporales y de E/S cuando un periférico concreto difiera.
5. Lo que **no cambia**:
   - el RTL de la biblioteca (`template/lib/`);
   - los diseños y pruebas del flujo abierto (`template/src/`, CocoTB, PyTest,
     Verilator, Icarus);
   - `make synth-all` con Yosys, que sigue siendo una comprobación lógica
     **agnóstica de placa** dentro de la familia Cyclone V y no sustituye el
     P&R ni el timing de Quartus.

## Consecuencias

- La plantilla pasa de “repo fijado a una sola tarjeta” a **repo configurable
  por curso/laboratorio**.
- Un profesor o estudiante podrá escoger `TARGET_BOARD=de1_soc` o
  `TARGET_BOARD=de10_standard` en Quartus/CI **sin tocar el RTL** del ejercicio
  o de la biblioteca.
- La documentación deja de presentar la DE1-SoC como requisito único y la
  reemplaza por una decisión más precisa: **el TFG opera sobre tarjetas Cyclone
  V SoC soportadas por plantilla**.
- El objetivo pedagógico mejora: si una cohorte tiene DE1-SoC y otra
  DE10-Standard, ambas pueden usar el mismo repositorio, los mismos testbenches
  y la misma compuerta de evidencia, evitando *forks* y reescrituras.
- El canal host↔FPGA de ADR-0004 sigue siendo válido en su idea central porque
  ambas placas comparten HPS Cyclone V y USB-Blaster II; lo que pasa a ser
  específico del *target* es la configuración exacta del proyecto, el pinout y
  la automatización del laboratorio.
- **Pendiente explícito:** antes de congelar un `.qsf` productivo para cada
  placa, el pinout físico final debe verificarse contra los manuales oficiales
  de Terasic. La plantilla puede llevar *placeholders* comentados donde todavía
  no exista verificación suficiente, siempre que no se presenten como datos
  confirmados.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Mantener DE1-SoC como plataforma única | Resuelve la trazabilidad histórica, pero no el requerimiento nuevo de reutilizar la plantilla en cursos con hardware distinto. |
| Crear un *fork* por tarjeta | Duplica mantenimiento, ADRs, CI y documentación; justo lo contrario del objetivo de plantilla reusable. |
| Pedir que cada estudiante edite el RTL según la placa | Mezcla infraestructura con diseño digital y rompe la separación entre biblioteca mantenida y personalización de despliegue. |
| Parametrizar solo la familia Cyclone V y no la placa concreta | Insuficiente: Quartus, el pinout, el runner de hardware y ciertos periféricos dependen de la tarjeta, no solo de la familia. |
