# Ejercicio didáctico: cómo hacer fallar y pasar la ALU

El RTL entregado en `template/lib/alu/alu.sv` **no contiene bugs activos** y
debe pasar la prueba. Este archivo documenta una mutación manual para que el
estudiante vea el valor del testbench.

## Bug intencional sugerido

En el `case` de `OP_SUB`, cambie temporalmente esta línea:

```systemverilog
result = a - b;
```

por esta versión rota:

```systemverilog
result = a + b;
```

Con ese cambio, la resta deja de comportarse como resta y el test dirigido de
overflow/borrow falla de inmediato.

## Cómo reproducir

```bash
make sim DESIGN=alu SIM=icarus TB_SEED=1234 TB_CYCLES=40
```

## Señal de que **pasa**

Salida esperada representativa:

```text
...
test_alu_matches_reference_model ... passed
...
```

## Señal de que **falla**

Con la mutación anterior, la salida representativa contiene un error como este:

```text
AssertionError: stimulus 3: result expected 127, got 129
stimulus 3: overflow expected 1, got 0
```

El índice del estímulo apunta al prefijo dirigido de `alu_sequence()`, por lo
que el estudiante puede correlacionar el fallo con el caso borde documentado.

## Restauración

Deshaga el cambio en `alu.sv` y repita el comando. El mismo test debe volver a
pasar sin tocar el checker ni el modelo.
