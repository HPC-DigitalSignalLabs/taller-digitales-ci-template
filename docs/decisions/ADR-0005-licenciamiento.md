# ADR-0005 — Licenciamiento del entregable

**Fase 0 · S2 · OE1 · Estado: ACEPTADA · Decidida por el autor **

## Contexto

El autor manifiesta preferencia por **GPLv3**.
El entregable no es homogéneo: contiene herramientas en Python, RTL en SystemVerilog,
definiciones de contenedor y *workflows* de CI, y está pensado como **plantilla que cada
estudiante clona y modifica**.

## 1. GPLv2 frente a GPLv3

Ambas son copyleft fuerte: quien distribuye un derivado debe publicarlo bajo la misma
licencia y entregar el código fuente. Las diferencias reales:

| Eje | GPLv2 (1991) | GPLv3 (2007) |
|---|---|---|
| **Patentes** | Sin concesión expresa. Solo una licencia implícita de contorno jurídico discutido | **§11: concesión expresa de patentes** por cada contribuyente. Protege a autor y usuarios |
| **Tivoización** | Permitida: se puede distribuir en un aparato que impida ejecutar versiones modificadas | **§6: prohibida** para "User Products". Hay que entregar la información de instalación |
| **Compatibilidad con Apache-2.0** | **Incompatible.** No se puede combinar código Apache-2.0 | **Compatible.** Se puede incorporar código Apache-2.0 |
| **Terminación por incumplimiento** | Inmediata y permanente; hace falta que el titular reinstale | **§8: plazo de subsanación** (30/60 días) para el primer incumplimiento |
| **Anti-DRM** | Sin previsión | **§3:** la obra no es una "medida tecnológica de protección" |
| **Redacción** | Terminología centrada en EE. UU. ("distribute") | Internacionalizada ("convey", "propagate") |

**Para este proyecto la diferencia que más pesa es la compatibilidad con Apache-2.0.**
Buena parte del ecosistema EDA abierto es permisivo (Verible es Apache-2.0; CocoTB es
BSD-3; FuseSoC y Edalize son BSD-2). Con GPLv3 se puede incorporar cualquiera de ellos.
Con GPLv2 no se podría incorporar código Apache-2.0. La concesión de patentes es el
segundo argumento, y ambos apuntan en la misma dirección.

**Conclusión parcial: entre GPLv2 y GPLv3, GPLv3 es la elección correcta.** La pregunta
tal como se formuló tiene una respuesta clara.

## 2. La pregunta más importante que no se hizo

Elegir entre v2 y v3 importa menos que elegir **si el entregable debe ser copyleft**.

### Objeción 1 — El copyleft se propaga al trabajo del estudiante

El entregable es una plantilla que el estudiante clona para hacer su proyecto en `src/`.
Bajo GPL, el resultado es plausiblemente una obra derivada, y el estudiante quedaría
obligado a publicar su tarea bajo GPLv3 si la distribuye.

Eso choca con el propósito del proyecto. La infraestructura debe habilitar el trabajo del
estudiante, no imponerle condiciones sobre su propia autoría, y menos en un curso donde la
entrega es evaluada.

### Objeción 2 — La GPL está redactada para software, no para hardware

Sobre RTL, sus conceptos centrales encajan mal. Qué es "enlazar" entre dos módulos
SystemVerilog, o si un *bitstream* sintetizado a partir de RTL bajo GPL es obra derivada,
son cuestiones no resueltas. Por eso la comunidad de hardware abierto creó licencias
específicas: **CERN-OHL-P/W/S v2** y **Solderpad Hardware License v2.1** (Apache-2.0 con
redacción adaptada a hardware).

La norma del ecosistema RISC-V es permisiva: Ibex/lowRISC usa Apache-2.0, CVA6 usa
Solderpad, Rocket usa Apache-2.0/BSD. Publicar un núcleo RV32I didáctico bajo GPL lo
aparta de esa norma sin ganancia.

## 3. Decisión

**Apache-2.0 de forma uniforme para todo el entregable** (`template/`, `tools/`,
`.github/workflows/`, `hpc/`, `lib/`). La documentación y la tesis quedan bajo
**CC BY-SA 4.0**, que es una licencia pensada para documentos y no para código.

### Por qué se descartó el copyleft

El entregable es una **plantilla que cada estudiante clona para hacer su tarea evaluada**.
Con GPLv3, el trabajo del estudiante pasa a ser plausiblemente una obra derivada, y el
estudiante queda obligado a publicar bajo copyleft un trabajo que es suyo y que además
está siendo calificado. Esa consecuencia no la eligió él: se la impuso la plantilla del
curso. Un requisito de licenciamiento no debería viajar de contrabando dentro de una
herramienta docente.

Hay un segundo problema, independiente: **el copyleft está redactado para software y no
traduce bien al hardware**. La GPL razona sobre "enlazado" y "obra derivada", conceptos
que no tienen equivalente claro en RTL. ¿Un bitstream que incluye un skid buffer de
`lib/` es una obra derivada de ese skid buffer? ¿Y el netlist sintetizado? No hay
jurisprudencia útil, y el ecosistema RISC-V ya resolvió esta ambigüedad en la práctica
optando por permisivo: Ibex, CVA6 y Rocket son Apache-2.0 o Solderpad (que es Apache-2.0
con una enmienda para hardware).

### Por qué Apache-2.0 y no MIT

Ambas son permisivas y ambas resuelven el problema anterior. Apache-2.0 aporta tres cosas
que MIT no tiene, y la primera es la que decide:

1. **Concesión expresa de patentes (§3).** Era el argumento principal a favor de GPLv3
   frente a GPLv2, y no hay razón para perderlo al volverse permisivo. Protege tanto al
   autor como a quien adopte la plantilla. MIT guarda silencio sobre patentes.
2. **Cláusula de represalia por patentes.** Quien demande por patentes pierde la licencia.
3. **Términos explícitos de contribución (§5) y `NOTICE`.** Relevante para un repositorio
   que se espera que reciba contribuciones de sucesivas generaciones del curso.

El costo es que Apache-2.0 es más larga que MIT. A cambio, es el estándar de facto del
ecosistema EDA abierto: Verible es Apache-2.0, y CocoTB (BSD-3), FuseSoC y Edalize (BSD-2)
son todas compatibles sin fricción.

### Objeción, y respuesta

*"Sin copyleft, alguien puede tomar la infraestructura, mejorarla y no devolver nada."*
Es cierto, y es el precio real de esta decisión. Se acepta por dos razones. La primera es
de proporción: el riesgo de apropiación de una plantilla docente universitaria es
pequeño, mientras que el perjuicio de encadenar la tarea calificada de un estudiante es
concreto y ocurre en cada curso. La segunda es que el mecanismo que de verdad hace volver
las mejoras aquí no es la licencia sino el repositorio: el curso mantiene `lib/` y las
mejoras entran por *pull request*, gobernadas por la protección de rama y el *pipeline
gatekeeper*, no por una obligación legal.

## 4. Verificación

- `LICENSE` (Apache-2.0) y `NOTICE` en la raíz del repositorio.
- `docs/LICENSE` con el texto de CC BY-SA 4.0 por referencia.
- Cabecera SPDX en cada archivo fuente (`// SPDX-License-Identifier: Apache-2.0`), que es
  lo que permite auditar la mezcla de forma automática y detectar código GPL entrante.
- El `README` explica en un párrafo que el diseño del estudiante es suyo.

## 5. Pendiente

**Consulta al asesor y a la coordinación del Taller.** El TEC puede tener política
institucional sobre la titularidad de los trabajos finales de graduación. Esa política
tiene prelación sobre esta decisión técnica: si existe, manda ella. Debe confirmarse
antes de la defensa, pero no bloquea el desarrollo, porque pasar de Apache-2.0 a otra
licencia permisiva es trivial y la decisión ya evita el caso problemático (copyleft sobre
el trabajo del estudiante).

## 6. Consecuencias

- `src/` queda cubierto por Apache-2.0 como el resto del árbol, pero eso **no le impone
  nada al estudiante**: una licencia permisiva no exige que el derivado se publique. El
  estudiante puede licenciar su diseño como quiera, o no licenciarlo.
- El `README` debe decirlo en un párrafo, porque el estudiante no va a leer el `LICENSE`.
- Toda incorporación futura de código GPL al entregable queda **vedada**: mezclarlo
  reintroduciría el copyleft por la puerta de atrás. Verificable con la cabecera SPDX.
