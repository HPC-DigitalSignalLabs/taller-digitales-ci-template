# ADR-0007: Alcance de biblioteca y operación del curso

> **Adenda de vigencia — 2026-10-16:** la selección de cuatro bloques del punto 2,
> el máximo de cuatro de la verificación y la ampliación ALU del 08/09 quedan
> **sustituidos como alcance de entrega**, no borrados, por las cuatro familias
> de ADR-0011 y la
> adenda del anteproyecto.
> La implementación previa se conserva. OE1–OE4 y estados del cuerpo siguiente
> son históricos; la operación docente compatible se mantiene, sin presumir
> disponibilidad de protecciones por plan ni una nueva aprobación institucional.

**Estado:** ACEPTADA  
**Fecha:** 2026-09-03  
**Decisores:** estudiante y contraparte académica  
**Afecta:** OE1, OE2, OE3 y OE4

## Contexto

La propuesta anterior incluía un núcleo RV32I, varios fenómenos de timing y una
capa de observabilidad basada en un servicio de *webhooks*. La contraparte
identificó tres riesgos:

1. El microprocesador requiere una especificación y una campaña de validación
   que desplazan el objetivo de infraestructura.
2. Explorar varias frecuencias aumenta el trabajo sin probar mejor el flujo.
3. El profesor carece de control y visibilidad central porque cada equipo crea
   su repositorio y debe agregarlo manualmente como colaborador.

También se confirmó que reducir barreras de acceso a la tecnología debe quedar
explícito en el proyecto.

## Decisión

1. La verificación usa Verilator, Icarus, CocoTB y PyTest. No se incorpora otro
   framework.
2. La biblioteca se limita a cuatro bloques: contador, skid buffer,
   sincronizador de dos etapas y detector de flanco.
3. Todos los diseños de referencia usan `CLOCK_50`, con periodo de 20 ns, como
   único objetivo temporal.
4. Los repositorios de equipo se aprovisionan dentro de una organización
   administrada por el curso. El profesorado conserva acceso central, reglas de
   protección y `CODEOWNERS`.
5. La observabilidad mínima se genera consultando la API de GitHub de forma
   programada y publicando un tablero estático. No se opera un servidor de
   *webhooks*.
6. Cada bloque de biblioteca se documenta con el OpenCE RTL Hardware
   Documentation Template.

## Consecuencias

- Se elimina el costo de implementar y validar una ISA.
- La evidencia se concentra en reproducibilidad, simulación, síntesis,
  trazabilidad y validación física.
- El margen temporal se reporta a 50 MHz. No se fuerza una falla elevando el
  reloj.
- El docente puede consultar el estado de la cohorte sin depender de
  invitaciones emitidas por estudiantes.
- La verificación abierta reduce barreras de instalación, licencia y capacidad
  local, pero Quartus sigue siendo necesario para place-and-route, bitstream y
  Timing Analyzer.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Núcleo RV32I mínimo | La validación funcional y de ISA excede el valor que aporta para demostrar el pipeline. |
| Varias frecuencias generadas por PLL | Amplía el espacio de pruebas y complica la demostración sin ser requisito de los objetivos. |
| Servicio permanente de *webhooks* | Requiere infraestructura y operación adicional. Una consulta programada satisface la observabilidad mínima. |
| Repositorios controlados por cada equipo | Mantiene el problema de acceso y control identificado por la contraparte. |

## Verificación de la decisión

- Búsqueda automática sin referencias activas al procesador en documentos
  vivos.
- Cuatro componentes como máximo, cada uno con RTL, pruebas y ficha OpenCE.
- SDC con `create_clock` de 20 ns sobre `CLOCK_50`.
- Aprovisionamiento de una cohorte de prueba y tablero que identifique equipos
  sin ejecución verde reciente.

## Adenda (2026-09-08): ampliación de biblioteca a 5 bloques

Se agrega una **ALU parametrizable** como quinto bloque de referencia de la
biblioteca. La justificación es didáctica y explícitamente solicitada por la
contraparte académica: mostrar un componente sencillo que permita ver con
claridad cómo una prueba pasa, cómo falla cuando se introduce una mutación
manual y cómo se personaliza la infraestructura base para un bloque nuevo.

Esta adenda no altera la decisión aceptada sobre el foco del proyecto:
reproducibilidad, simulación, síntesis abierta, trazabilidad y validación
física siguen siendo el objetivo central, y la ALU se incorpora precisamente
para reforzar esa demostración.
