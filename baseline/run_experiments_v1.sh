#!/usr/bin/env bash
# Single formal runner for baseline_v1 (CSI500 mid-cap momentum / volume-price breakout).
# One config -> one experiment dir under output/baseline_v1/.
# Usage:
#   bash baseline/run_experiments_v1.sh           # full v1 sweep (excludes smoke mock)
#   DRY_RUN=1 bash baseline/run_experiments_v1.sh # dry-run only
#
# Smoke (mock fetcher, no network) is intentionally separate; not run here.

set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG_DIR="baseline/experiments_v1"
DRY_FLAG=""
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  DRY_FLAG="--dry-run"
fi

CONFIGS=(
  "v1_1_lookback20_vol1.5_topk20"
  "v1_2_lookback40_vol1.5_topk20"
  "v1_3_lookback20_vol2.0_topk20"
  "v1_4_lookback20_vol1.5_topk10"
  "v1_5_lookback20_vol1.5_topk30"
  "v1_6_lookback60_vol1.5_topk20"
  "v1_7_lookback20_vol1.0_topk20"
)

for name in "${CONFIGS[@]}"; do
  echo "==== running ${name} ${DRY_FLAG} ===="
  python baseline/run_baseline.py --config "${CONFIG_DIR}/${name}.yaml" --fold 0 ${DRY_FLAG}
done
