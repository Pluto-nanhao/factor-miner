#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"

if [[ "${FACTOR_MINER_ALLOW_OVERLAP:-false}" != "true" ]] && pgrep -af 'src.baseline.local_factor_miner' | grep -v grep | grep -q 'src.baseline.local_factor_miner'; then
  echo "REFUSED: local factor miner is already running:" >&2
  pgrep -af 'src.baseline.local_factor_miner' | grep -v grep >&2 || true
  exit 1
fi

cd "${REPO_ROOT}"

mkdir -p "${REPO_ROOT}/logs"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LAUNCH_LOG="${REPO_ROOT}/logs/factor_mining_v1_${STAMP}.log"
NEXT_DIRECTION_JSON="${REPO_ROOT}/output/next_factor_direction.json"
NEXT_DIRECTION_MD="${REPO_ROOT}/output/next_factor_direction.md"

"${PY}" scripts/generate_next_factor_direction.py \
  --work-dir "${FACTOR_MINER_WORK_DIR:-/mnt/storage/work/hwang}" \
  --out "${NEXT_DIRECTION_JSON}" \
  --md-out "${NEXT_DIRECTION_MD}" \
  >>"${LAUNCH_LOG}" 2>&1

export FACTOR_MINER_CODEGEN_PARALLEL="${FACTOR_MINER_CODEGEN_PARALLEL:-6}"
export FACTOR_MINER_DISCUSSION_AGENTS="${FACTOR_MINER_DISCUSSION_AGENTS:-3}"
export FACTOR_MINER_DISCUSSION_PARALLEL="${FACTOR_MINER_DISCUSSION_PARALLEL:-true}"
export FACTOR_MINER_ASYNC_FEEDBACK="${FACTOR_MINER_ASYNC_FEEDBACK:-true}"
export FACTOR_MINER_CODEX_MODEL="${FACTOR_MINER_CODEX_MODEL:-gpt-5.4}"
export FACTOR_MINER_DISCUSSION_MODEL="${FACTOR_MINER_DISCUSSION_MODEL:-gpt-5.4}"
export FACTOR_MINER_FEEDBACK_MODEL="${FACTOR_MINER_FEEDBACK_MODEL:-gpt-5.4}"
export FACTOR_MINER_PRESCREEN="${FACTOR_MINER_PRESCREEN:-true}"
export FACTOR_MINER_PRESCREEN_START_DATE="${FACTOR_MINER_PRESCREEN_START_DATE:-20220101}"
export FACTOR_MINER_PRESCREEN_MIN_SHARPE="${FACTOR_MINER_PRESCREEN_MIN_SHARPE:-2.5}"
export FACTOR_MINER_PRESCREEN_MIN_RET="${FACTOR_MINER_PRESCREEN_MIN_RET:-15.0}"
export FACTOR_MINER_PRESCREEN_TIMEOUT="${FACTOR_MINER_PRESCREEN_TIMEOUT:-420}"
export FACTOR_MINER_NEXT_DIRECTION_JSON="${NEXT_DIRECTION_JSON}"
export FACTOR_MINER_USE_DYNAMIC_DIRECTION="${FACTOR_MINER_USE_DYNAMIC_DIRECTION:-true}"
export FACTOR_MINER_ENABLE_FORECAST_DATA="${FACTOR_MINER_ENABLE_FORECAST_DATA:-false}"
export FACTOR_MINER_ENABLE_CC_ALL_EXTRA_DATA="${FACTOR_MINER_ENABLE_CC_ALL_EXTRA_DATA:-false}"
export FACTOR_MINER_NIODATAPATH="${FACTOR_MINER_NIODATAPATH:-/datasvc/data/cc}"
export FACTOR_MINER_REQUIRE_NEW_DIRECTION_PER_ROUND="${FACTOR_MINER_REQUIRE_NEW_DIRECTION_PER_ROUND:-true}"
export FACTOR_MINER_RECENT_DIRECTION_EXCLUDE_LIMIT="${FACTOR_MINER_RECENT_DIRECTION_EXCLUDE_LIMIT:-64}"

nohup setsid "${PY}" -m src.baseline.local_factor_miner \
  --seed IntraDay \
  --iters "${ITERS:-40}" \
  --parallel "${PARALLEL:-8}" \
  --timeout "${TIMEOUT:-900}" \
  >"${LAUNCH_LOG}" 2>&1 &
PID=$!

sleep 1
echo "STARTED ${PID} ${LAUNCH_LOG}"
