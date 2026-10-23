# Piloto de imagen actual y contrato de orquestación

Preparación del 6 de octubre de 2026 (S10, Fase 3, OE2/OE3).
La preparación por sí sola **no certifica una ejecución en Kabré**. El piloto
posterior `618035`, workflow `37498061791`, terminó PASS 1/1 con ambos
simuladores; hashes, límites y evidencia están en
`docs/despliegue-workflows-aprobados.md` (raíz del repositorio).
Autoridad:
`docs/anteproyecto.md` §3.3, §3.5 fila OE2 y §4.2.
Procedimiento y política: `docs/campana-kabre.md` §5 y
`docs/fase1/kabre.md` §7.1.

## 1. Gates antes de actuar

- Publicación CI exitosa del commit elegido; obtener su digest OCI real.
  No usar una rama mutable ni reutilizar el ejemplo histórico `1a5bbc…`.
- SIF preparado fuera de Kabré. `apptainer pull docker://…` convierte OCI a
  SIF y puede consumir CPU/RAM: **no ejecutarlo en login**. La documentación
  pide consultar a CeNAT si se pretende convertir dentro del clúster; una
  asignación de cómputo por sí sola no constituye esa autorización. Sin ella,
  convertir en CI/estación Linux y transferir el SIF. No añadir un build al job.
- Cuenta y acceso existentes; no crear claves, servicios ni runners.
  Host documentado `kabre.cenat.ac.cr`, puerto `22022`, alias `kabre`,
  `User shidalgo` en el ejemplo y `IdentityFile ~/.ssh/id_ed25519_kabre`.
  Confirmar usuario real y host key. Usar `BatchMode=yes` y
  `StrictHostKeyChecking=yes`; nunca desactivar verificación.
- Consultar **toda** la cola del usuario antes de cualquier carga nueva.
  Si hay trabajos activos/pendientes, esperar y revisar, no enviar otro lote.
- GitHub solo invoca este controlador en un host Linux de envío SLURM ya
  autorizado y preaprovisionado. No se instala un runner persistente en login.

## 2. Imagen inmutable (fuera del clúster)

Los placeholders son obligatorios: el digest no se puede inferir del SHA Git.
Ejecutar en Linux con Apptainer ya disponible, no en login de Kabré:

```bash
set -euo pipefail
export SOURCE_SHA='<40-hex-del-commit-publicado>'
export OCI_DIGEST='sha256:<64-hex-del-digest-publicado>'
export SIF="$PWD/digital-design-verify-${SOURCE_SHA}.sif"
test ! -e "$SIF"
apptainer pull "$SIF" \
  "docker://ghcr.io/katharsis0/tfg/digital-design-verify@${OCI_DIGEST}"
sha256sum "$SIF"
```

Guardar URI OCI, commit, run de publicación y SHA256 del SIF como evidencia.
**Digest OCI y SHA256 del archivo SIF son distintos.** Un nombre de archivo
no acredita procedencia. Si GHCR es privado, usar autenticación de lectura
ya autorizada; no incluir tokens en comandos/logs ni en el repo.

Transferencia autorizada por el integrador, una vez creado el directorio
remoto privado `/work/<usuario>/images`:

```bash
scp -P 22022 -o BatchMode=yes -o StrictHostKeyChecking=yes \
  "$SIF" 'kabre:/work/<usuario>/images/'
```

## 3. Configuración mínima y planificación

Crear un JSON **fuera de la instantánea de fuentes**, con los valores
observados/revisados por el integrador (no copiar los placeholders):

```json
{
  "partition": "kura-wide",
  "account": "tec",
  "qos": "normal",
  "module": "apptainer/1.2.5",
  "module_init": "/opt/Modules/3.2.10/init/sh",
  "runtime": "apptainer",
  "image": "/work/<usuario>/images/digital-design-verify-<commit>.sif",
  "image_sha256": "<SHA256-del-SIF-64-hex>",
  "results_root": "/work/<usuario>/campaign-results",
  "cpus": 1,
  "memory_mb": 2048,
  "time_minutes": 5,
  "max_tasks": 1,
  "max_concurrency": 1,
  "max_cpus": 1,
  "max_memory_mb": 2048,
  "max_time_minutes": 5
}
```

`results_root` debe ser común para todos los envíos del mismo usuario:
aloja el lock que serializa la comprobación de cola y el envío. El plan
debe usar un directorio nuevo por ejecución. Planificar desde un checkout
Git real; las tareas usan la instantánea sin `.git`, no vuelven a planificar.
Preferir checkout limpio; `--allow-dirty` es una excepción explícita tras
revisar `source.dirty_status` y el digest de fuentes, no un valor por defecto.

En el host remoto autorizado de envío (rutas ajustadas por el integrador):

```bash
set -euo pipefail
cd /home/<usuario>/TFG
export TOOL="$PWD/template/tools/hpc_campaign.py"
export PLAN='/work/<usuario>/campaigns/current-image-pilot-UNICO'
export CONFIG='/work/<usuario>/current-image-pilot.json'
sinfo -h -p kura-wide -o '%P|%D|%c'
squeue --noheader --array --user "$USER" --format='%i|%j|%T'
# Detenerse si la cola no está vacía; el submit vuelve a comprobarla.
python3 "$TOOL" plan --config "$CONFIG" --source "$PWD" --output "$PLAN" \
  --mode demo --batch-size 1 --repetitions 1 --concurrency 1 \
  --seed 1234 --cycles 200
python3 "$TOOL" submit --manifest "$PLAN/manifest.json"
# Revisar manifiesto, hash SIF y argv antes de ejecutar:
python3 "$TOOL" submit --manifest "$PLAN/manifest.json" --execute --require-idle
```

Argv esperado: `--nodes=1 --ntasks=1 --array=0-0%1 --cpus-per-task=1
--mem=2048M --time=5 --no-requeue --export=NONE`. Solicitud máxima:
una tarea, una CPU, 2048 MiB, cinco minutos de ejecución
(cinco CPU-minutos nominales). SLURM puede asignar más CPU por granularidad:
registrar `AllocCPUS`, no atribuir una asignación exacta por la solicitud.

**`--mode demo` ejecuta counter WIDTH=8, secuencialmente en Verilator e
Icarus, 200 ciclos/semilla 1234; no ejecuta el full-demo de biblioteca ni
Graphviz/Yosys.** Ésta es una prueba del ciclo HPC, no validación completa
del contenido de la imagen ni de OE3/OE4.

## 4. Espera y evidencia para GitHub

```bash
python3 "$TOOL" wait --manifest "$PLAN/manifest.json" \
  --timeout-seconds 600 --poll-seconds 15
```

Contrato:

- `submit` conserva salida humana; leer `submission.json`, no extraer ID
  de stdout. Recibo: `job_id`, `cluster`, `manifest_sha256`, `argv`,
  `stdout`, `stderr`, `returncode`.
- `wait` imprime **un JSON completo final** y guarda `summary.json`.
  No envía, cancela, escala ni reintenta jobs. Timeout de espera incluye
  cola y visibilidad de evidencia; no modifica los cinco minutos SLURM.
  Si SLURM ya registra ejecución exitosa pero task.json/JUnit aún no es
  válido o visible en el filesystem compartido, vuelve a consultar dentro
  del mismo plazo. No relaja hashes ni convierte evidencia inválida en PASS:
  si no se valida antes del límite, devuelve NOT_PASS/timeout. Los fallos
  terminales de SLURM siguen terminando la espera sin repetir el job.
- Exit `0`: `PASS`. Exit `1`: estado terminal no satisfactorio o plazo
  agotado. Exit `2`: error de controlador/consulta. Nunca convertir estos
  fallos en verde con `|| true`; recuperar artefactos en un paso `always()`
  de GitHub separado del resultado del job.
- `wait_status`: `terminal`, `timeout` o `collection-error`.
  Resumen normal: `schema=1`, `job_id`, `complete`, `verdict`,
  `manifest_sha256`, `source.{git_sha,dirty,digest}`, `image_sha256`,
  `expected_tasks`, `successful_tasks`, `states`, `tasks`, latencias de
  cola/ejecución/total y advertencias. Cada tarea incluye estado/ExitCode
  SLURM y validación JUnit. Ante error de consulta no se inventan tareas:
  se publica `NOT_PASS`, `complete=false` y `error`, invalidando un PASS
  previo.
- Cada consulta se conserva en `accounting/*.json`; el fallo real de
  make/PyTest/runtime se conserva en `task.json` (`process_returncode`,
  `exit_code`) y sale no cero del worker, incluso si fue una señal.
- Recoger siempre manifiesto, recibo/reclamación, `summary.json`,
  `accounting/`, `logs/` y el directorio
  `/work/<usuario>/campaign-results/<job_id>/` con `task.json` y resultados.
  Los artefactos de GitHub no deben contener claves, tokens ni configs SSH.
- `collect --manifest …` sigue disponible para consulta única o evidencia
  offline (`--sacct-file` y `--results-root`). Exit `1` también significa
  pendiente/incompleto: no equivale por sí solo a fallo terminal del diseño.

Si se agota la espera, el job puede seguir activo. Inspeccionar el ID exacto
del recibo y recolectar después; cancelar **solo ese ID** únicamente con
autorización. No borrar claims/locks para repetir automáticamente un envío
ambiguo. `--require-idle` no impide envíos ajenos que evadan el lock; el
integrador debe evitar otra carga simultánea.

**Objeción:** esperar ocupa un runner. **Respuesta:** es un sondeo de
contabilidad acotado, no cómputo de simulación; se evita añadir un servicio
o scheduler paralelo. Si no hay acceso/SIF actual, conservar el smoke del
pipeline gatekeeper y declarar el piloto bloqueado, no ejecutarlo con una
imagen antigua y llamarlo reproducibilidad 1:1.

Validación local (sin SLURM real):

```bash
python3 -m unittest discover -s template/tests/hpc -p test_hpc_campaign.py -v
```
