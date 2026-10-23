# Cómo contribuir

Gracias por mejorar la plantilla. Todo cambio entra por pull request y pasa
por el gatekeeper y la cola de merge; nadie (tampoco los administradores)
puede empujar directamente a `main`.

## Flujo

1. Crear una rama desde `main` (o un fork si no tienes permiso de escritura).
2. Ejecutar localmente, dentro del DevContainer:
   ```bash
   make smoke
   make library-regression
   make synth-all
   ```
3. Abrir el PR con la plantilla de descripción. El check requerido es
   `Gatekeeper verdict`.
4. Cuando esté verde (y aprobado si toca `.github/`), pulsar
   **Merge when ready**: el PR entra en la cola, que vuelve a verificar la
   combinación con `main` y con los PRs que van delante.
5. Si la cola expulsa el PR, leer el run del grupo de merge: indica una
   incompatibilidad con otro cambio ya integrado o en cola.

Mensajes de commit al estilo *Conventional Commits*
(`feat(rtl): ...`, `fix(ci): ...`, `docs: ...`, `test: ...`).

## Agregar un bloque

| Archivo | Contenido | Comprobado por |
|---|---|---|
| `lib/<b>/<b>.sv` | RTL SystemVerilog, `default_nettype none`, SPDX | Verilator `-Wall`, Icarus, Yosys |
| `tests/<b>/model.py` | Modelo de referencia en Python puro | `tests/convention/` |
| `tests/<b>/stimulus.py` | Estímulos deterministas con semilla | `tests/convention/` |
| `tests/<b>/checker.py` | Comparación; **sin** cocotb ni drivers | `tests/convention/` |
| `tests/<b>/tb_<b>.py` | Testbench CocoTB delgado | Simulación en ambos simuladores |
| `tests/<b>/test_<b>.py` | Pruebas unitarias + simulación parametrizada | `make library-regression` |
| `docs/modules/<b>.md` | Ficha OpenCE (secciones obligatorias) | `tests/integration/test_library_surface.py` |

Registrar el nombre en `LIBRARY_DESIGNS`, en `SYNTH_ALL_JSON`/`synth-all`,
`netlist-all` (`Makefile`), en `sim.mk` (fuentes y parámetro) y en `DESIGNS`
de `tests/integration/test_library_surface.py`. Estas listas son el único
punto compartido entre bloques: si dos PRs agregan bloques a la vez, el
segundo deberá resolver un conflicto trivial en esas líneas.

## Qué no se acepta

- Código bajo licencias copyleft dentro del árbol (ver ADR-0005).
- Ejecutar EDA propietario en CI o en el clúster.
- Desactivar, omitir o marcar como `continue-on-error` un check obligatorio.
- Reutilizar evidencia de otra ejecución para volver verde un resultado.
