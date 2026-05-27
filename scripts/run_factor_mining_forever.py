#!/usr/bin/env python3
"""Continuously run aggressive factor mining rounds until the process is stopped."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
OUT_ROOT = ROOT / "output" / "factor_mining"


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def latest_run_dir() -> Path | None:
    runs = sorted(OUT_ROOT.glob("factor_mining_IntraDay_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def parse_started(output: str) -> tuple[int | None, Path | None]:
    match = re.search(r"STARTED\s+(\d+)\s+(\S+)", output)
    if not match:
        return None, None
    return int(match.group(1)), Path(match.group(2))


def parse_run_dir(launch_log: Path | None) -> Path | None:
    if launch_log is None or not launch_log.exists():
        return None
    text = launch_log.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"run_dir=(.+)", text)
    return Path(match.group(1).strip()) if match else None


def result_count(run_dir: Path | None) -> int:
    if run_dir is None:
        return 0
    path = run_dir / "results.csv"
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def run_target_iters(run_dir: Path | None, env: dict[str, str]) -> int:
    fallback = int(env["ITERS"])
    if run_dir is None:
        return fallback
    status_path = run_dir / "run_status.json"
    if not status_path.exists():
        return fallback
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        value = int(data.get("args", {}).get("iters", fallback))
    except Exception:
        return fallback
    return value if value > 0 else fallback


def run_stats(run_dir: Path | None) -> dict:
    if run_dir is None:
        return {}
    status_path = run_dir / "run_status.json"
    if not status_path.exists():
        return {}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        stats = data.get("stats", {})
    except Exception:
        return {}
    return stats if isinstance(stats, dict) else {}


def active_gsim_count() -> int:
    try:
        rows = subprocess.check_output(
            ["pgrep", "-af", r"/usr/local/gsim/.*/run.py"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except Exception:
        return 0
    return len([row for row in rows if "/usr/local/gsim/" in row and "run.py" in row])


def overlap_ready(run_dir: Path | None, completed: int, target_iters: int, env: dict[str, str]) -> bool:
    min_results = int(env["FACTOR_MINER_OVERLAP_MIN_RESULTS"])
    if completed < min(min_results, target_iters):
        return False
    stats = run_stats(run_dir)
    codegen_submitted = int(stats.get("codegen_submitted", 0) or 0)
    if codegen_submitted < target_iters:
        return False
    return active_gsim_count() < int(env["FACTOR_MINER_OVERLAP_MAX_ACTIVE_GSIM"])


def candidate_kind(iter_dir: Path, factor: str) -> str:
    if (iter_dir / f"Config.tinyclaw.neg.xml").exists() and factor.endswith("Neg"):
        return "neg"
    if "Decay" in factor:
        suffix = factor.rsplit("Decay", 1)[-1]
        if suffix.isdigit() and (iter_dir / f"Config.tinyclaw.decay{suffix}.xml").exists():
            return f"decay{suffix}"
    if (iter_dir / f"Config.tinyclaw.xml").exists():
        return "base"
    return "base"


def build_rescue_args(run_dir: Path) -> list[str]:
    path = run_dir / "rescue_candidates.csv"
    if not path.exists():
        return []
    args = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            reason = row.get("reason", "")
            if row.get("accepted") == "True" or reason == "accepted_and_installed":
                continue
            iteration = int(row["iteration"])
            factor = row["factor"]
            iter_dir = run_dir / f"iter_{iteration:03d}"
            if not iter_dir.exists():
                continue
            kind = candidate_kind(iter_dir, factor)
            args.extend(["--candidate", f"{iter_dir}:{factor}:{kind}"])
    return args


def reap_rescues(running: list[tuple[int, Path, subprocess.Popen[str]]]) -> list[tuple[int, Path, subprocess.Popen[str]]]:
    alive: list[tuple[int, Path, subprocess.Popen[str]]] = []
    for round_no, run_dir, proc in running:
        rc = proc.poll()
        if rc is None:
            alive.append((round_no, run_dir, proc))
            continue
        log(f"round {round_no} async rescue finished rc={rc} run_dir={run_dir}")
    return alive


def start_async_rescue(round_no: int, run_dir: Path, env: dict[str, str]) -> tuple[int, Path, subprocess.Popen[str]] | None:
    rescue_args = build_rescue_args(run_dir)
    if not rescue_args:
        log(f"round {round_no} no rescue candidates")
        return None
    out = run_dir / "continuous_rescue.csv"
    rescue_log = run_dir / "continuous_rescue.log"
    cmd = [
        "/usr/local/gsim/.venv/bin/python",
        str(ROOT / "scripts" / "rescue_specific_candidates.py"),
        "--parallel",
        str(len(rescue_args) // 2),
        "--out",
        str(out),
        *rescue_args,
    ]
    log(f"round {round_no} async rescue candidates={len(rescue_args)//2} log={rescue_log}")
    with rescue_log.open("a", encoding="utf-8") as f:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True, stdout=f, stderr=subprocess.STDOUT)
    return round_no, run_dir, proc


def launch_round(round_no: int, env: dict[str, str]) -> dict[str, object] | None:
    launch_env = env.copy()
    launch_env["FACTOR_MINER_ALLOW_OVERLAP"] = "true"
    log(f"round {round_no} start")
    proc = subprocess.run(["./baseline/run_factor_mining_v1.sh"], cwd=ROOT, env=launch_env, text=True, capture_output=True)
    output = proc.stdout.strip() or proc.stderr.strip() or f"round {round_no} launch returned {proc.returncode}"
    log(output)
    if proc.returncode != 0:
        return None
    pid, launch_log = parse_started(output)
    return {"round_no": round_no, "pid": pid, "launch_log": launch_log, "run_dir": None}


def any_local_miner_running() -> bool:
    return subprocess.run(["pgrep", "-f", "src.baseline.local_factor_miner"], capture_output=True).returncode == 0


def pid_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0


def refresh_round_run_dir(active_round: dict[str, object]) -> Path | None:
    if active_round.get("run_dir") is None:
        active_round["run_dir"] = parse_run_dir(active_round.get("launch_log"))  # type: ignore[arg-type]
    run_dir = active_round.get("run_dir")
    return run_dir if isinstance(run_dir, Path) else None


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("FACTOR_MINER_CODEX_MODEL", "gpt-5.4")
    env.setdefault("FACTOR_MINER_DISCUSSION_MODEL", "gpt-5.4")
    env.setdefault("FACTOR_MINER_FEEDBACK_MODEL", "gpt-5.4")
    env.setdefault("FACTOR_MINER_CODEGEN_PARALLEL", "6")
    env.setdefault("FACTOR_MINER_DISCUSSION_AGENTS", "3")
    env.setdefault("FACTOR_MINER_DISCUSSION_PARALLEL", "true")
    env.setdefault("FACTOR_MINER_ASYNC_FEEDBACK", "true")
    env.setdefault("FACTOR_MINER_AGENT_TIMEOUT", "240")
    env.setdefault("FACTOR_MINER_AGENT_RETRIES", "2")
    env.setdefault("FACTOR_MINER_PRESCREEN", "true")
    env.setdefault("FACTOR_MINER_PRESCREEN_START_DATE", "20220101")
    env.setdefault("FACTOR_MINER_PRESCREEN_MIN_SHARPE", "2.5")
    env.setdefault("FACTOR_MINER_PRESCREEN_MIN_RET", "15.0")
    env.setdefault("FACTOR_MINER_PRESCREEN_TIMEOUT", "420")
    env.setdefault("FACTOR_MINER_ENABLE_FORECAST_DATA", "false")
    env.setdefault("FACTOR_MINER_ENABLE_CC_ALL_EXTRA_DATA", "false")
    env.setdefault("FACTOR_MINER_NIODATAPATH", "/datasvc/data/cc")
    env.setdefault("FACTOR_MINER_MAX_ACTIVE_MAIN_ROUNDS", "2")
    env.setdefault("FACTOR_MINER_OVERLAP_MIN_RESULTS", "110")
    env.setdefault("FACTOR_MINER_OVERLAP_MAX_ACTIVE_GSIM", "8")
    env.setdefault("ITERS", "120")
    env.setdefault("PARALLEL", "12")
    env.setdefault("TIMEOUT", "900")

    round_no = 0
    running_rescues: list[tuple[int, Path, subprocess.Popen[str]]] = []
    active_rounds: list[dict[str, object]] = []

    existing = latest_run_dir()
    if existing is not None:
        existing_results = result_count(existing)
        if any_local_miner_running() and existing_results < int(env["ITERS"]):
            log(f"adopting active existing run instead of launching duplicate: {existing} results={existing_results}")
            active_rounds.append({"round_no": 0, "pid": None, "launch_log": None, "run_dir": existing})
        elif existing_results >= int(env["ITERS"]):
            log(f"adopting completed-main existing run for overlap handoff: {existing}")
            rescue = start_async_rescue(0, existing, env)
            if rescue is not None:
                running_rescues.append(rescue)

    while True:
        running_rescues = reap_rescues(running_rescues)
        max_active_rounds = max(1, int(env["FACTOR_MINER_MAX_ACTIVE_MAIN_ROUNDS"]))
        if not active_rounds:
            round_no += 1
            launched = launch_round(round_no, env)
            if launched is None:
                log("launch failed; sleep 60s")
                time.sleep(60)
                continue
            active_rounds.append(launched)
            time.sleep(5)
            continue

        still_active: list[dict[str, object]] = []
        for active_round in active_rounds:
            run_dir = refresh_round_run_dir(active_round)
            completed = result_count(run_dir)
            target_iters = run_target_iters(run_dir, env)
            pid = active_round.get("pid")
            miner_alive = pid_running(pid) or (pid is None and any_local_miner_running())
            if completed < target_iters and not miner_alive:
                log(
                    f"round {active_round['round_no']} abandoned: miner pid is not running "
                    f"and results incomplete ({completed}/{target_iters}); dropping active handle"
                )
                continue

            if completed >= target_iters:
                finished_round = int(active_round["round_no"])
                if run_dir is not None:
                    rescue = start_async_rescue(finished_round, run_dir, env)
                    if rescue is not None:
                        running_rescues.append(rescue)
                log(
                    f"round {finished_round} main results complete ({completed}); "
                    "launching next round without waiting for internal rescue"
                )
                continue

            still_active.append(active_round)
        active_rounds = still_active

        if not active_rounds:
            continue

        can_launch_overlap = False
        if len(active_rounds) < max_active_rounds:
            for active_round in active_rounds:
                run_dir = refresh_round_run_dir(active_round)
                completed = result_count(run_dir)
                target_iters = run_target_iters(run_dir, env)
                if overlap_ready(run_dir, completed, target_iters, env):
                    log(
                        f"round {active_round['round_no']} overlap-ready "
                        f"({completed}/{target_iters}, active_gsim={active_gsim_count()}); "
                        "launching another main round"
                    )
                    can_launch_overlap = True
                    break
        if can_launch_overlap:
            round_no += 1
            launched = launch_round(round_no, env)
            if launched is not None:
                active_rounds.append(launched)
                time.sleep(5)
                continue
            log(
                f"overlap launch failed; active_rounds={len(active_rounds)} "
                "will retry after sleep"
            )

        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
