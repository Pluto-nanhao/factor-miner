#!/usr/bin/env bash
# AI-oriented launcher and quick-reference for tinyKaggleClaw.
#
# This script is intentionally explicit: it teaches an automation agent which
# commands are the canonical entry points, where logs are written, and how to
# avoid starting duplicate factor-mining workers.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

PY="${ROOT}/.venv/bin/python"
GSIM_PY="${GSIM_PY:-/usr/local/gsim/.venv/bin/python}"
RUNTIME_CONFIG="${ROOT}/research_mvp/runtime.toml"
LOG_DIR="${ROOT}/logs"
FACTOR_OUT="${ROOT}/output/factor_mining"

mkdir -p "${LOG_DIR}" "${FACTOR_OUT}"

if [[ -x "${ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.venv/bin/activate"
fi

section() {
  printf '\n== %s ==\n' "$1"
}

is_running() {
  pgrep -af "$1" >/dev/null 2>&1
}

print_matches() {
  local pattern="$1"
  local label="$2"
  if is_running "${pattern}"; then
    echo "${label}: running"
    pgrep -af "${pattern}" || true
  else
    echo "${label}: stopped"
  fi
}

latest_log() {
  local glob="$1"
  find "${LOG_DIR}" -maxdepth 1 -type f -name "${glob}" -printf '%T@ %p\n' \
    | sort -nr \
    | awk 'NR==1 {print $2}'
}

latest_rescue_run() {
  find "${FACTOR_OUT}" -maxdepth 2 -type f -name rescue_candidates.csv -printf '%T@ %h\n' \
    | sort -nr \
    | awk 'NR==1 {print $2}'
}

guide() {
  cat <<EOF
tinyKaggleClaw AI startup guide

Run from repo root:
  cd ${ROOT}

Main commands:
  ./ai_start.sh guide          Print this guide.
  ./ai_start.sh status         Show runtime, miner, rescue, and useful URLs.
  ./ai_start.sh start          Start Research MVP services and factor-mining daemon.
  ./ai_start.sh runtime        Start only Research MVP services.
  ./ai_start.sh miner          Start one factor-mining round if none is running.
  ./ai_start.sh forever        Start the factor-mining daemon if not already running.
  ./ai_start.sh replicate      Start the factors.directory replication daemon.
  ./ai_start.sh rescue-last    Rescue latest run that has rescue_candidates.csv.
  ./ai_start.sh logs           Tail the most relevant logs.

Canonical project entry points:
  Research MVP:       ./start_research_mvp.sh start
  Runtime status:     ./start_research_mvp.sh status
  One mining round:   ./baseline/run_factor_mining_v1.sh
  Mining daemon:      ${PY} scripts/run_factor_mining_forever.py
  Replication daemon: ${PY} scripts/run_factors_directory_replication_forever.py
  Manual rescue:      ${GSIM_PY} scripts/rescue_specific_candidates.py ...

Default ports:
  Runtime board:      http://127.0.0.1:8090/runtime
  Training queue:     http://127.0.0.1:8100/

Important logs:
  Research runtime:   logs/research_mvp_runtime.log
  Runtime web app:    logs/research_mvp_app.log
  Train service:      logs/research_mvp_train_service.log
  Factor mining:      logs/factor_mining_v1_*.log
  Mining daemon:      logs/factor_mining_forever.log
  Replication daemon: logs/factors_directory_replication.log
  Parallel rescue:    logs/parallel_rescue_*.log

Important output:
  Factor runs:        output/factor_mining/factor_mining_IntraDay_*

Safety rule:
  Do not start another local_factor_miner while one is running. The canonical
  miner script enforces this. The forever daemon will wait and retry.

Parallel rule:
  Run independent work with bounded parallelism. Default caps are 8 workers
  for backtest/rescue and 2 workers for Codex codegen. Keep discussion
  agents sequential unless explicitly requested.
EOF
}

status() {
  section "Processes"
  print_matches "research_mvp.runtime_cli" "runtime supervisor"
  print_matches "uvicorn research_mvp.app:app" "runtime web app"
  print_matches "uvicorn research_mvp.train_service.app:app" "train service"
  print_matches "src.baseline.local_factor_miner" "factor miner"
  print_matches "run_factor_mining_forever.py" "factor-mining daemon"
  print_matches "run_factors_directory_replication_forever.py" "factors.directory replication daemon"
  print_matches "rescue_specific_candidates.py" "manual rescue"

  section "Services"
  echo "Runtime board:  http://127.0.0.1:8090/runtime"
  echo "Training queue: http://127.0.0.1:8100/"

  section "Latest Logs"
  echo "factor miner:   $(latest_log 'factor_mining_v1_*.log' || true)"
  echo "daemon:         ${LOG_DIR}/factor_mining_forever.log"
  echo "replication:    ${LOG_DIR}/factors_directory_replication.log"
  echo "parallel rescue: $(latest_log 'parallel_rescue_*.log' || true)"

  section "Latest Rescue Run"
  latest_rescue_run || true
}

start_runtime() {
  ./start_research_mvp.sh start "${TASK_TYPE:-}"
}

start_miner() {
  if is_running "src.baseline.local_factor_miner"; then
    echo "factor miner already running:"
    pgrep -af "src.baseline.local_factor_miner" || true
    return 0
  fi
  ./baseline/run_factor_mining_v1.sh
}

start_forever() {
  if is_running "run_factor_mining_forever.py"; then
    echo "factor-mining daemon already running:"
    pgrep -af "run_factor_mining_forever.py" || true
    return 0
  fi
  nohup setsid "${PY}" scripts/run_factor_mining_forever.py >> "${LOG_DIR}/factor_mining_forever.log" 2>&1 &
  echo "STARTED $! ${LOG_DIR}/factor_mining_forever.log"
}

start_replication() {
  if is_running "run_factors_directory_replication_forever.py"; then
    echo "factors.directory replication daemon already running:"
    pgrep -af "run_factors_directory_replication_forever.py" || true
    return 0
  fi
  nohup setsid env \
    FACTOR_REPLICATION_ITERS="${FACTOR_REPLICATION_ITERS:-40}" \
    FACTOR_REPLICATION_PARALLEL="${FACTOR_REPLICATION_PARALLEL:-8}" \
    FACTOR_REPLICATION_CODEGEN_PARALLEL="${FACTOR_REPLICATION_CODEGEN_PARALLEL:-2}" \
    FACTOR_REPLICATION_DISCUSSION_PARALLEL="${FACTOR_REPLICATION_DISCUSSION_PARALLEL:-false}" \
    "${PY}" scripts/run_factors_directory_replication_forever.py >> "${LOG_DIR}/factors_directory_replication.log" 2>&1 &
  echo "STARTED $! ${LOG_DIR}/factors_directory_replication.log"
  echo "Ledger: ${ROOT}/output/factors_directory_replication/replication_ledger.csv"
}

start_all() {
  start_runtime
  start_forever
  start_replication
  status
}

rescue_last() {
  local run_dir
  run_dir="$(latest_rescue_run || true)"
  if [[ -z "${run_dir}" ]]; then
    echo "No run with rescue_candidates.csv found under ${FACTOR_OUT}" >&2
    return 1
  fi

  local stamp out log
  stamp="$(date -u +%Y%m%d_%H%M%S)"
  out="${run_dir}/parallel_rescue_${stamp}.csv"
  log="${LOG_DIR}/parallel_rescue_${stamp}.log"

  mapfile -t candidates < <("${PY}" - <<PY
import csv
from pathlib import Path

run = Path("${run_dir}")
for row in csv.DictReader((run / "rescue_candidates.csv").open(newline="", encoding="utf-8")):
    if row.get("accepted") == "True" or row.get("reason") == "accepted_and_installed":
        continue
    iter_dir = run / ("iter_%03d" % int(row["iteration"]))
    factor = row["factor"]
    kind = "base"
    if (iter_dir / "Config.tinyclaw.neg.xml").exists() and factor.endswith("Neg"):
        kind = "neg"
    elif "Decay" in factor:
        suffix = factor.rsplit("Decay", 1)[-1]
        if suffix.isdigit() and (iter_dir / ("Config.tinyclaw.decay%s.xml" % suffix)).exists():
            kind = "decay%s" % suffix
    print(f"--candidate={iter_dir}:{factor}:{kind}")
PY
  )

  if [[ "${#candidates[@]}" -eq 0 ]]; then
    echo "No unresolved rescue candidates in ${run_dir}"
    return 0
  fi

  nohup setsid "${GSIM_PY}" scripts/rescue_specific_candidates.py \
    --parallel "${RESCUE_PARALLEL:-8}" \
    --out "${out}" \
    "${candidates[@]}" \
    >> "${log}" 2>&1 &

  echo "STARTED $! ${log}"
  echo "Output CSV: ${out}"
}

logs() {
  section "factor-mining daemon"
  tail -n 80 "${LOG_DIR}/factor_mining_forever.log" 2>/dev/null || true

  local miner_log
  miner_log="$(latest_log 'factor_mining_v1_*.log' || true)"
  if [[ -n "${miner_log}" ]]; then
    section "latest factor miner: ${miner_log}"
    tail -n 80 "${miner_log}" || true
  fi

  local rescue_log
  rescue_log="$(latest_log 'parallel_rescue_*.log' || true)"
  if [[ -n "${rescue_log}" ]]; then
    section "latest parallel rescue: ${rescue_log}"
    tail -n 80 "${rescue_log}" || true
  fi

  section "factors.directory replication"
  tail -n 80 "${LOG_DIR}/factors_directory_replication.log" 2>/dev/null || true
}

usage() {
  guide
  cat <<'EOF'

Usage:
  ./ai_start.sh [guide|status|start|runtime|miner|forever|replicate|rescue-last|logs]
EOF
}

cmd="${1:-guide}"
case "${cmd}" in
  guide)
    guide
    ;;
  status)
    status
    ;;
  start)
    start_all
    ;;
  runtime)
    start_runtime
    ;;
  miner)
    start_miner
    ;;
  forever)
    start_forever
    ;;
  replicate)
    start_replication
    ;;
  rescue-last)
    rescue_last
    ;;
  logs)
    logs
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
