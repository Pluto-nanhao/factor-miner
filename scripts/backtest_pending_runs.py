#!/usr/bin/env python3
"""Backtest all currently generated but unrecorded candidates for run dirs."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.baseline import local_factor_miner as miner  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def recorded_iterations(run_dir: Path) -> set[int]:
    path = run_dir / "results.csv"
    if not path.exists():
        return set()
    rows = csv.DictReader(path.open("r", encoding="utf-8", newline=""))
    out: set[int] = set()
    for row in rows:
        try:
            out.add(int(row["iteration"]))
        except Exception:
            continue
    return out


def factor_name_from_xml(xml_path: Path) -> str | None:
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'<Alpha id="([^"]+)"', text)
    return match.group(1) if match else None


def command_running(path: Path) -> bool:
    return subprocess.run(["pgrep", "-f", str(path)], capture_output=True).returncode == 0


def candidate_jobs(run_dir: Path) -> list[tuple[int, str, Path, Path, Path]]:
    done = recorded_iterations(run_dir)
    jobs: list[tuple[int, str, Path, Path, Path]] = []
    for iter_dir in sorted(run_dir.glob("iter_*")):
        try:
            iteration = int(iter_dir.name.removeprefix("iter_"))
        except ValueError:
            continue
        if iteration in done:
            continue
        xml_path = iter_dir / "Config.tinyclaw.xml"
        if not xml_path.exists() or command_running(xml_path):
            continue
        factor_name = factor_name_from_xml(xml_path)
        if not factor_name:
            continue
        jobs.append((iteration, factor_name, xml_path, iter_dir / "pnl" / factor_name, iter_dir / "gsim.log"))
    return jobs


def already_recorded(run_dir: Path, iteration: int) -> bool:
    return iteration in recorded_iterations(run_dir)


def run_job(run_dir: Path, job: tuple[int, str, Path, Path, Path], timeout: int) -> tuple[int, str]:
    iteration, factor_name, _xml, _pnl, _log = job
    if already_recorded(run_dir, iteration):
        return iteration, "skipped_already_recorded"
    _it, final_name, _rc, metrics, err, install_status = miner.run_one(job, timeout)
    if already_recorded(run_dir, iteration):
        return iteration, "skipped_recorded_by_owner"
    miner.record_result(run_dir, _it, final_name, metrics, err, install_status)
    return iteration, str(err or (install_status or {}).get("reason", "recorded"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="+", type=Path)
    parser.add_argument("--parallel", type=int, default=0, help="worker count; <=0 means all pending jobs")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    miner._RUN_LOG = None
    miner.ensure_research_notes()
    total = 0
    for run_dir in args.run_dir:
        run_dir = run_dir.resolve()
        jobs = candidate_jobs(run_dir)
        if not jobs:
            log(f"{run_dir}: no pending generated jobs")
            continue
        workers = len(jobs) if args.parallel <= 0 else min(args.parallel, len(jobs))
        total += len(jobs)
        log(f"{run_dir}: pending={len(jobs)} parallel={workers}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_job, run_dir, job, args.timeout) for job in jobs]
            for future in as_completed(futures):
                try:
                    iteration, status = future.result()
                    log(f"{run_dir.name} iter_{iteration:03d}: {status}")
                except Exception as exc:  # noqa: BLE001
                    log(f"{run_dir.name}: exception {type(exc).__name__}: {exc}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
