# bin2bcd — conversión binaria sin signo a BCD

## Revisions

| Fecha | Cambio |
|---|---|
| 2026-10-16 | Primer bloque de la familia I/O, E1/OE2 vigente. |

## Document Conventions

`bcd[3:0]` contiene unidades, `bcd[7:4]` decenas, etc.
Una cifra BCD es un nibble que representa 0–9, no un dígito hexadecimal.
La entrada binaria es **sin signo**.

## Introduction

Conversión combinacional reutilizable, sin reloj, handshakes ni multiplexado
de displays. Junto con [`bcd_to_7seg`](bcd_to_7seg.md) forma una única familia
I/O de E1 (§V3); criterio verificable OE2 (§V4). No implementa el demo.

## Block Diagram

```mermaid
flowchart LR
    binary["binary WIDTH bits"] --> DD["double-dabble combinacional"]
    DD --> bcd["bcd: DIGITS nibbles"]
```

## Configuration

| Nombre | Contrato |
|---|---|
| `WIDTH` | Entero 1..64, por defecto 8 |
| `binary` | Entrada de WIDTH bits |
| `bcd` | Salida de `4*((WIDTH+2)/3)` bits |
| `DIGITS` | Localparam interno `(WIDTH+2)/3`; **no sobreescribible** |

`DIGITS=ceil(WIDTH/3)` es una cota conservadora: como `2**3<10`,
`10**DIGITS > 2**WIDTH-1`. Nunca se pierde el dígito más significativo.
Puede haber ceros extra a la izquierda: WIDTH 8 produce 3 cifras,
WIDTH 32 produce 11, WIDTH 64 produce 22. WIDTH 1 produce una cifra.
No se ofrecen capacidad de salida configurable ni truncamiento.

WIDTH fuera de 1..64 se rechaza con `$fatal`. El límite 64 acota coste de
la lógica desplegada y la matriz de validación; no es una limitación de BCD.
El modelo Python rechaza también valores que no caben en WIDTH.

## Protocols

La salida representa el valor actual de `binary` después de propagación.
Ejemplo WIDTH 8: `255 → 12'h255`, `0 → 12'h000`.
Todos los nibbles son decimales válidos. No hay flags ni saturación.
Una salida de ceros a la izquierda no implica supresión de dígitos visuales:
esa política correspondería al sistema integrador.

## Memory Map

No aplica.

## Registers

No aplica: no hay estado.

## Clock Domains

Bloque combinacional. El integrador decide registros de entrada/salida y
restricciones de sus caminos. La espera de 20 ns del testbench no mide
retardo físico ni garantiza 50 MHz.

## Reset Domains

No aplica: no existe puerto de reset; la salida es función de la entrada.

## Interrupts

No aplica.

## Arbitration

No aplica.

## Debugging

Comparar entrada decimal y cada nibble, empezando por unidades. Un nibble
10..15 es fallo. Comprobar WIDTH efectivo, no solo la variable Python.
Puede mostrar transitorios mientras cambia un bus; no es un circuito CDC.

## Synthesis

```sh
make synth DESIGN=bin2bcd WIDTH=8
make synth DESIGN=bin2bcd WIDTH=32
make netlist DESIGN=bin2bcd WIDTH=8
```

Double-dabble estándar (shift-add-3) desplegado, coste aproximadamente
proporcional a WIDTH×DIGITS; síntesis elimina lógica constante.
WIDTH 1 puede reducirse casi por completo a conexiones/constantes.
No se exige una cuenta mínima de LUT que penalice esa optimización.
Los anchos grandes pueden tener un camino combinacional largo:
Quartus debe medirlo con el SDC de la integración; no se promete timing.

## Verification

```sh
python -m pytest tests/bin2bcd -m "not simulation" -v
python -m pytest tests/bin2bcd -m simulation -v
make sim DESIGN=bin2bcd WIDTH=64 SIM=verilator
make sim DESIGN=bin2bcd WIDTH=1 SIM=icarus
```

Modelo por divisiones decimales (`divmod`), independiente del double-dabble.
Pruebas Python exhaustivas para WIDTH 1..12, capacidad y máximo de cada
WIDTH 1..64. El driver comprueba ambos anchos de puerto.
Matriz RTL: WIDTH 1, 4, 8, 16, 32 y 64, en Verilator e Icarus.
Para anchos ≤10 el estímulo recorre todas las entradas; para mayores,
fronteras de potencias de 2/10 ±1, cero/máximo y 500 valores aleatorios
con `TB_SEED=1234`. Checker independiente del transporte.

Validación local 2026-10-16: 86 pruebas Python y 12 simulaciones PASS
(CocoTB 1.9.2, Verilator 5.052 e Icarus 13.0); mapeo Yosys 0.66 w1/w8/w32
y SVG w8 generados. No acredita reproducibilidad 1:1 con la imagen fijada.
Los casos negativos comprueban detección de pérdida de centenas, dígitos
inválidos y exceso de capacidad; no son una campaña de mutación RTL.

## Microarchitecture

Un `always_comb` inicializa toda la salida a cero, visita cada bit de
entrada desde el MSB, suma tres a cada nibble ≥5 y desplaza incorporando
el siguiente bit binario. Bucles de límites constantes, sin latch.

**Objeción:** una conversión secuencial utiliza menos lógica.
**Respuesta:** exige reloj, protocolo y latencia; la referencia combinacional
separa conversión y control sin imponer el demo. Si el timing real no cumple,
se documentará el límite y se decidirá una alternativa en la integración,
no se elevará el reloj para forzar un fallo ni se ocultará truncamiento.

## References

- Anteproyecto, adenda vigente §§V3–V4.
- RTL: `template/lib/bin2bcd/bin2bcd.sv`.
- Pruebas: `template/tests/bin2bcd/`.
- [OpenCE RTL Hardware Documentation Template](https://opencehardware.github.io/RTL-Hardware-Documentation-Template/).
