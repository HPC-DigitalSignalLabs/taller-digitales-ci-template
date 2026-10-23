# ADR-0006 — Compuerta de artefacto de síntesis ligada al commit

**Fase 0 · S2 · OE2, OE4 · Estado: ACEPTADA · 2026-08-13**

## Contexto

### De dónde viene esta exigencia

Vale dejarlo por escrito, porque no es obvio y ya generó una duda legítima: **la exigencia es
del anteproyecto, no una reacción al repositorio del curso.** Dos pasajes la fijan.

En §Arquitectura, describiendo la comprobación (3) del *gatekeeper*:

> Para que la evidencia de síntesis sea confiable y no meramente un archivo que el estudiante
> pudo fabricar o dejar obsoleto, la validación (3) se diseña para ser *verificable*: el
> reporte se ata al *hash* del commit que se está evaluando (el flujo de síntesis local
> registra el commit y el estado del árbol de trabajo en el momento de generarlo), y el
> trabajo de CI rechaza el artefacto si no corresponde a ese commit o si el árbol de trabajo
> no estaba limpio al generarlo. […] Así, un reporte heredado de otra versión del diseño, o
> editado a mano, no pasa la compuerta.

Y en la definición del *dry run* de OE4, que enumera seis escenarios; el quinto es
**«artefacto obsoleto o no correspondiente al commit»**. La métrica de aceptación asociada es
`6/6 escenarios correctos (incl. rechazo de artefacto obsoleto…)`.

Es decir: sin esta compuerta, OE4 no se puede cumplir tal como está redactado. La observación
de que los repositorios del curso usan `grep -q "Successful"`
(`docs/fase0/observacion-repos-curso.md` §3.1) fue el **disparador temporal** del trabajo y una
fuente útil de casos de prueba, pero no su origen ni su justificación.

### El problema técnico

Quartus tiene licencia *node-locked*: no corre en GitHub Actions ni en Kabré. CI recibe por
tanto un reporte de síntesis que **no puede regenerar** y debe decidir si es evidencia
admisible para el commit que está evaluando. Es una situación asimétrica poco común en CI:
todas las demás comprobaciones recomputan su propio resultado.

## Decisión

**El flujo local emite un registro de procedencia (*sidecar*) junto al reporte, y CI valida
el par reporte + procedencia contra el commit evaluado.**

- `quartus/post_flow.tcl` corre al final de la compilación (vía `POST_FLOW_SCRIPT_FILE`) y
  escribe `<reporte>.provenance.json` con: el commit, si el árbol estaba sucio, el *blob id*
  de cada fuente compilada, el *blob id* del propio reporte, y metadatos de herramienta.
- `tools/artifact_gate.py` valida ese *sidecar*, con códigos de rechazo estables mapeados a
  los escenarios del *dry run*: `MISSING_ARTIFACT` (escenario 3), `SYNTHESIS_FAILED` (4),
  `STALE_ARTIFACT` (5), más `MALFORMED_PROVENANCE`, `DIRTY_TREE`, `TAMPERED_REPORT` y
  `GATE_INTERNAL`.

### Reglas de diseño que no son negociables

1. **El TCL no calcula ni un solo hash.** Todo *blob id* es un `git hash-object` producido por
   git, tanto al escribir el *sidecar* como al verificarlo. Los dos lados coinciden por
   construcción y no hay criptografía escrita en TCL.
2. **La ligadura al commit es por ancestría, no por igualdad.** Exigir `procedencia.commit ==
   HEAD` es *insatisfacible*: commitear el artefacto crea un SHA nuevo por definición, de modo
   que la igualdad rechaza todo artefacto honesto. Fue un defecto real, encontrado por revisión
   adversarial y reproducido con repositorios git antes de corregirlo. La obsolescencia real la
   detecta la **comparación *blob* a *blob* de cada fuente**.
3. **`GATE_INTERNAL` es distinto de un rechazo.** «La evidencia es mala» y «no pudimos leer la
   evidencia» son cosas distintas; confundirlas vuelve inmapeable la matriz de *dry run* y manda
   al estudiante a perseguir la falla equivocada.
4. **Fallar cerrado.** En toda ruta de rechazo el TCL **no emite *sidecar***, y la compuerta
   trata el *sidecar* ausente como fallo. La propiedad de seguridad no depende del código de
   salida del script.

## Justificación

La alternativa barata —comprobar que el reporte existe y dice `Successful`— no puede satisfacer
el escenario 5 **ni siquiera en principio**, porque el reporte no viaja acompañado de ningún
dato sobre el commit ni sobre el estado del árbol: la comprobación no tiene con qué decidir. El
problema no es la robustez del `grep`; es la ausencia de información. Por eso la solución es
emitir información nueva, no endurecer la comparación.

## Modelo de amenaza (y su límite honesto)

La compuerta defiende contra **sustitución accidental y de bajo esfuerzo**: un reporte de una
revisión anterior, uno producido con el árbol sucio, uno editado a mano para que diga
`Successful`.

**No es una prueba criptográfica de origen.** El *sidecar* se produce en una máquina que el
estudiante controla; quien esté dispuesto a escribir un *sidecar* válido a mano puede hacerlo.
Esto se declara en el *docstring* del módulo y aquí, y no se disimula.

**Vía de mejora que elimina el supuesto:** un *runner* auto-hospedado en el laboratorio que
ejecute la síntesis **dentro** de CI. Ahí el artefacto deja de ser evidencia aportada y pasa a
ser evidencia generada, y toda esta maquinaria se vuelve innecesaria. Está fuera del alcance de
esta versión porque depende de infraestructura y de política institucional, no de código.

## Alternativas descartadas

| Alternativa | Razón |
|---|---|
| `grep -q "Successful"` sobre el reporte | No puede satisfacer el escenario 5 de OE4: no hay información que ligue el reporte al commit |
| Firmar el reporte con una clave del estudiante | La clave también está en la máquina del estudiante; añade gestión de claves sin cambiar el modelo de amenaza |
| Hash propio calculado en TCL | Duplica lo que git ya hace y abre una vía de divergencia entre productor y verificador |
| Exigir `procedencia.commit == HEAD` | Matemáticamente insatisfacible (ver Decisión, regla 2) |
| No versionar el artefacto y confiar en la palabra del estudiante | Elimina la comprobación (3) del *gatekeeper* y con ella una métrica de OE2 |

## Consecuencias

- El artefacto de síntesis y su *sidecar* **se versionan en Git**; el `.gitignore` los exceptúa
  explícitamente de las reglas de Quartus.
- La compuerta introduce un acoplamiento entre `post_flow.tcl` (productor) y
  `tools/artifact_gate.py` (verificador). El contrato es el esquema de `provenance.py`, con
  `SCHEMA_VERSION`, y hay pruebas de acuerdo *end-to-end* entre ambos lados.
- **Costo asumido:** ~1 150 líneas entre productor, verificador y pruebas, escritas en Fase 0
  para un entregable de Fase 2. Se acepta porque resolvió de paso el supuesto de mayor riesgo
  del proyecto (que Quartus se maneje sin interacción) y porque produjo el arnés de pruebas que
  Fase 2 iba a necesitar igualmente. **No se amplía**: cualquier comprobación nueva sobre
  reportes (*latches*, *slack*) es trabajo de Fase 2, no de Fase 0.

## Verificación

1. Las siete rutas de rechazo tienen prueba, contra repositorios git reales
   (`tests/gate/test_artifact_gate.py`).
2. `post_flow.tcl` corre bajo `tclsh` con las cuatro APIs de Quartus simuladas
   (`tests/gate/test_post_flow_tcl.py`), y sus pruebas se validaron **por mutación**:
   reintroducir cada defecto conocido mata al menos una prueba.
3. Todo *blob id* del *sidecar* es igual a lo que produce `git hash-object` — es la prueba de
   que el script no calcula hashes por su cuenta.
4. **Pendiente:** ninguna de estas pruebas ha corrido con Quartus real. El acuerdo entre las
   APIs simuladas y las reales se cierra en la primera síntesis en una estación de laboratorio.
