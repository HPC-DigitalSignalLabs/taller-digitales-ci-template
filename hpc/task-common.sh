#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Solo inicializa el runtime. La configuración está en el manifiesto, no en
# variables heredadas de una sesión interactiva.
set -euo pipefail
[[ -n "${SLURM_JOB_ID:-}" ]] || { echo "Se requiere una asignación SLURM." >&2; exit 2; }
MODULE_INIT=$(python3 "$TOOL" config-value --manifest "$MANIFEST" --key module_init)
MODULE_NAME=$(python3 "$TOOL" config-value --manifest "$MANIFEST" --key module)
if [[ -n "$MODULE_INIT" ]]; then
    [[ -r "$MODULE_INIT" ]] || { echo "No existe module_init: $MODULE_INIT" >&2; exit 2; }
    source "$MODULE_INIT"
fi
if [[ -n "$MODULE_NAME" ]]; then
    type module >/dev/null 2>&1 || { echo "No se inicializó modules." >&2; exit 2; }
    module load "$MODULE_NAME"
fi
