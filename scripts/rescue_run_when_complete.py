#!/usr/bin/env python3
"""Wait for a factor-mining run to finish, then rescue unresolved candidates."""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def run_finished(run_dir: Path, expected_results: int | None) -> bool:
    launch = run_dir / "launch.log"
    if launch.exists() and "\ndone\n" in launch.read_text(encoding="utf-8", errors="replace"):
        return True
    results = run_dir / "results.csv"
    if expected_results is not None and results.exists():
        with results.open("r", encoding="utf-8", newline="") as f:
            return sum(1 for _ in csv.DictReader(f)) >= expected_results
    return False


def candidate_kind(iter_dir: Path, factor: str) -> str:
    if (iter_dir / "Config.tinyclaw.neg.xml").exists() and factor.endswith("Neg"):
        return "neg"
    if factor.endswith("Rescue") and (iter_dir / "Config.tinyclaw.rescue.xml").exists():
        return "rescue"
    if "Decay" in factor:
        suffix = factor.rsplit("Decay", 1)[-1]
        if suffix.isdigit() and (iter_dir / f"Config.tinyclaw.decay{suffix}.xml").exists():
            return f"decay{suffix}"
    return "base"


def build_rescue_args(run_dir: Path) -> list[str]:
    rescue_path = run_dir / "rescue_candidates.csv"
    if not rescue_path.exists():
        return []
    args: list[str] = []
    with rescue_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("accepted") == "True" or row.get("reason") == "accepted_and_installed":
                continue
            try:
                iteration = int(row["iteration"])
            except Exception:
                continue
            factor = row.get("factor", "")
            iter_dir = run_dir / f"iter_{iteration:03d}"
            if factor and iter_dir.exists():
                args.extend(["--candidate", f"{iter_dir}:{factor}:{candidate_kind(iter_dir, factor)}"])
    return args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--expected-results", type=int, default=None)
    parser.add_argument("--poll", type=int, default=30)
    parser.add_argument("--parallel", type=int, default=8, help="worker count; <=0 explicitly means all rescue candidates")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--python", default="/usr/local/gsim/.venv/bin/python")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    out = args.out or run_dir / "continuous_rescue.csv"
    log(f"watching run_dir={run_dir}")
    while not run_finished(run_dir, args.expected_results):
        time.sleep(args.poll)

    rescue_args = build_rescue_args(run_dir)
    if not rescue_args:
        log("run finished; no unresolved rescue candidates")
        return 0

    cmd = [
        args.python,
        str(ROOT / "scripts" / "rescue_specific_candidates.py"),
        "--parallel",
        str(args.parallel if args.parallel > 0 else len(rescue_args) // 2),
        "--out",
        str(out),
        *rescue_args,
    ]
    log(f"run finished; rescue candidates={len(rescue_args) // 2} out={out}")
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.stdout.strip():
        log(proc.stdout.strip())
    if proc.returncode != 0:
        log(proc.stderr.strip() or f"rescue failed rc={proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
