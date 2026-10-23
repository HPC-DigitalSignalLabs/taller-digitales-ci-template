#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Usa el mismo aislamiento que regression.slurm y el mismo make sim que CI.
# Enviar mediante tools/hpc_campaign.py submit; logs existen antes de sbatch.
#SBATCH --job-name=demo-toolchain
#SBATCH --nodes=1
#SBATCH --ntasks=1
set -euo pipefail
umask 077
[[ $# == 2 ]] || { echo "Uso interno: demo-showcase.slurm TOOL MANIFEST" >&2; exit 2; }
TOOL=$1
MANIFEST=$2
ROOT=$(dirname "$(dirname "$TOOL")")
source "$ROOT/hpc/task-common.sh"
exec python3 "$TOOL" run-task --manifest "$MANIFEST" --mode demo
