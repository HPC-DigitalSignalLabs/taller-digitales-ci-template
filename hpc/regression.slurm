#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Enviar mediante tools/hpc_campaign.py submit; no usar sbatch directamente.
# El lanzador prepara logs antes de sbatch y pasa rutas absolutas.
#SBATCH --job-name=rtl-regression
#SBATCH --nodes=1
#SBATCH --ntasks=1
set -euo pipefail
umask 077
[[ $# == 2 ]] || { echo "Uso interno: regression.slurm TOOL MANIFEST" >&2; exit 2; }
TOOL=$1
MANIFEST=$2
ROOT=$(dirname "$(dirname "$TOOL")")
source "$ROOT/hpc/task-common.sh"
exec python3 "$TOOL" run-task --manifest "$MANIFEST" --mode regression
