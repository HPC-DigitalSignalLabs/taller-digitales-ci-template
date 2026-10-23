# ADR-0002 — Capa de verificación: Verilator + CocoTB + PyTest, sin framework UVM

**Fase 0 · S2 · OE1 · Estado: ACEPTADA · 2026-08-11**

## Contexto

OE3 exige que los estímulos y los *checkers* escritos para simulación se reutilicen
contra el silicio a través del canal JTAG, y mide esa reutilización
(`≥X%` de secuencias reutilizadas sin modificación). OE2 exige `flaky rate ≈0` y
reproducibilidad 1:1 entre el DevContainer local, Kabré y los *runners* hospedados.

La pregunta es qué capa de verificación sostiene esas métricas. La industria de
verificación funcional está estandarizada en **UVM**, y existe un puerto en Python,
**PyUVM**, que se integra con CocoTB. La objeción a no usarlo es real y hay que
enfrentarla, no esquivarla: CocoTB *pelado* obliga a re-derivar de forma *ad hoc* lo
que las secuencias, los *drivers* y el *scoreboard* de UVM resuelven de manera
canónica. Quien haya visto crecer un testbench sin estructura sabe cómo termina.

Hay además un dato de contexto que pesa sobre la decisión: el entregable no se dirige a
un equipo de verificación profesional sino al **Taller de Diseño Digital, el primer curso
de diseño digital de la carrera**, y debe poder crecer hacia diseños más complejos en
cursos posteriores.

## Decisión

**La capa de verificación es Verilator + CocoTB + PyTest, y nada más. PyUVM y
cualquier framework de estilo UVM quedan explícitamente fuera del alcance.**

La estructura que UVM aportaría se sustituye por una **convención de testbench fija**,
definida en Fase 1 y aplicada a todo módulo de la biblioteca de macros. Cada testbench
se descompone en cuatro archivos con una responsabilidad cada uno:

| Archivo | Responsabilidad | Restricción de importación |
|---|---|---|
| `model.py` | Modelo de referencia, Python puro | No importa `cocotb` ni ningún driver |
| `stimulus.py` | Generación de estímulo, con semilla explícita | No importa `cocotb` |
| `checker.py` | Comparación modelo vs DUT | **No importa `cocotb` ni ningún driver** |
| `tb_<diseño>.py` | Driver de interfaz: lo único que conoce el DUT | Importa `cocotb` |

La propiedad que sostiene todo esto es la tercera fila: **el `checker` es agnóstico al
transporte**. El mismo archivo juzga la traza que produce el simulador y la traza que
produce la FPGA por JTAG. Esa es, literalmente, la métrica de reutilización de OE3;
no es una preferencia estética.

Convención de nombres asociada: los testbenches de CocoTB se llaman `tb_<diseño>.py`,
nunca `test_*.py`, porque PyTest recolectaría el archivo e intentaría `import cocotb`
fuera del entorno del simulador. `test_<diseño>.py` queda reservado para la capa
PyTest, que prueba `model`/`stimulus`/`checker` en Python puro y delega la simulación
a `make sim`.

## Justificación

PyUVM queda fuera por **tres razones independientes**, ordenadas por peso. Las dos primeras
bastan por sí solas; la tercera sólo confirma.

### 1. Destinatario: es el primer curso de diseño digital

La plantilla la usan estudiantes que están construyendo intuición sobre lógica combinacional
y secuencial, y sobre la relación entre el RTL y lo que ocurre en el silicio. UVM es una
metodología de verificación **avanzada**, concebida para equipos que verifican SoCs completos.
Su aparato —fases, fábricas, `uvm_component`, `sequence_item`, TLM— excede por un orden de
magnitud la complejidad de los diseños que el curso ejercita: un *parity checker*, un sumador,
un contador, un skid buffer.

Imponerlo desplazaría la carga cognitiva del estudiante **desde el hardware hacia el andamiaje
del testbench**, que es exactamente lo contrario del propósito de este flujo. El estudiante
debe pelear con metaestabilidad y con *slack*, no con el orden de las fases de UVM.

### 2. Técnica: UVM obstaculiza la métrica que OE3 mide

Esta razón decide por sí sola, y es la que conviene defender en una revisión.

OE3 mide que **el mismo `checker` juzgue la traza del simulador y la traza que la FPGA
devuelve por JTAG**. La estructura de UVM está acoplada al simulador *por construcción*: las
secuencias ejercitan el DUT a través de un `uvm_driver` sobre una interfaz simulada, y el
`scoreboard` consume transacciones de esa misma jerarquía de componentes.

Reutilizar esa estructura contra un canal JTAG exigiría escribir una capa de adaptación cuyo
único propósito sería **recuperar la independencia del transporte que un módulo Python plano
tiene de entrada**. Frente a la métrica que este proyecto sí mide, UVM no es neutral: estorba.
La convención de cuatro archivos obtiene la propiedad gratis, porque `checker.py` no importa
nada.

### 3. Presupuesto (la menos importante)

Ninguna métrica de la tabla de aceptación requiere un testbench estructurado en UVM —las de
calidad de verificación son *mutation kill rate* (≥80%), concordancia sim/hardware (100% en
diseños correctos) y detección de divergencia inyectada— y la curva de adopción no se recupera
en un cronograma de 16 semanas con un solo desarrollador.

### Lo que esta decisión NO afirma

No afirma que UVM sea malo, ni que no valga la pena aprenderlo. La descomposición en estímulo,
modelo de referencia y comprobador **es la misma que UVM formaliza**; la convención de cuatro
archivos es, en ese sentido, UVM sin el andamiaje. Un curso posterior con diseños de mayor
complejidad puede adoptar PyUVM sobre esta misma base sin rehacer la infraestructura. **El
flujo se diseña para poder crecer hacia ahí, no para excluirlo.**

## Alternativas descartadas

| Alternativa | Razón |
|---|---|
| PyUVM sobre CocoTB | Aparato desproporcionado para el primer curso de diseño digital; su acoplamiento al simulador estorba la reutilización sim→hardware que OE3 mide; costo no recuperable en 16 semanas |
| UVM en SystemVerilog con Questa | Questa es de licencia *node-locked*: no puede correr en Kabré ni en GitHub Actions. Rompería la reproducibilidad 1:1 y el requisito de software abierto en el HPC |
| CocoTB sin convención, testbench por testbench | Es exactamente el escenario que la objeción anticipa: glue *ad hoc*, cero reutilización, y OE3 no se puede medir |
| Escribir un mini-framework propio de secuencias | Desarrollo de infraestructura de verificación no es un OE; sería alcance inventado |

## Consecuencias

- **Riesgo asumido:** que la reutilización estímulo/checker resulte insuficiente en
  CocoTB pelado. **Mitigación:** fijar la convención en Fase 1, antes de escribir el
  segundo testbench, y mantener el `checker` agnóstico al transporte.
- **Si la métrica de reutilización de OE3 no alcanza su umbral, se registra como
  hallazgo, no se amplía el alcance introduciendo un framework a mitad de camino.**
  Cambiar de capa de verificación en Fase 2 o 3 costaría más que el déficit medido.
- Cuando un testbench acumule *glue ad hoc*, la respuesta correcta es **endurecer la
  convención**, no alcanzar un framework a mitad de camino. Adoptar PyUVM es una decisión de
  una versión posterior, tomada con el flujo ya estable, no un parche de Fase 2.
- Salemi, *Python for RTL Verification*, queda como referencia canónica **solo en su
  parte de CocoTB**. Sus capítulos de PyUVM están fuera de alcance.

## Verificación

La decisión se considera correctamente implementada cuando, para todo módulo de la
biblioteca de macros:

1. `checker.py` no contiene `import cocotb` ni importa driver alguno — comprobable con
   una prueba automática, no por inspección.
2. El mismo `checker` se invoca desde el testbench de simulación y desde la validación
   por JTAG, y `compare_logs()` produce el veredicto de concordancia sim/hardware.
3. Toda prueba es determinista y con semilla registrada y reproducible.

---

**Nota de revisión (S4):** esta ADR descarta **UVM/PyUVM** como capa de
verificación, pero eso no impide ejecutar la misma capa **CocoTB + PyTest** con
otro simulador que CocoTB ya soporte de forma nativa. Desde S4, el template
documenta `SIM=questa` como backend opcional para ejecución **solo local** en
una máquina de laboratorio con Siemens Questa/ModelSim licenciado. La decisión
de fondo no cambia: no se incorpora un framework nuevo, el testbench Python
sigue siendo agnóstico del simulador, y el dominio abierto de CI/Kabré se
mantiene en Verilator/Icarus.
