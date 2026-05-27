#!/usr/bin/env python3
"""Run standard install/rescue flow on existing historical candidate jobs."""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.baseline import local_factor_miner as miner  # noqa: E402


def make_job(iter_dir: Path, factor_name: str, kind: str) -> tuple[int, str, Path, Path, Path]:
    iteration = int(iter_dir.name.removeprefix("iter_"))
    if kind == "base":
        return iteration, factor_name, iter_dir / "Config.tinyclaw.xml", iter_dir / "pnl" / factor_name, iter_dir / "gsim.log"
    return (
        iteration,
        factor_name,
        iter_dir / f"Config.tinyclaw.{kind}.xml",
        iter_dir / f"pnl_{kind}" / factor_name,
        iter_dir / f"gsim.{kind}.log",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", action="append", required=True, help="iter_dir:factor_name:kind")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=8, help="worker count; <=0 explicitly means all candidates")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    def run_candidate(item: str) -> dict[str, object]:
        iter_dir_s, factor_name, kind = item.rsplit(":", 2)
        job = make_job(Path(iter_dir_s), factor_name, kind)
        row = {
            "factor": factor_name,
            "kind": kind,
            "sharpe": "",
            "ret_pct": "",
            "tvr_pct": "",
            "accepted": False,
            "reason": "",
            "max_corr": "",
        }
        try:
            _it, _name, xml_path, pnl_file, log_path = job
            if not pnl_file.exists():
                rc = miner.run_gsim(xml_path, log_path, args.timeout)
                if rc != 0:
                    row["reason"] = f"run_failed rc={rc} log={log_path}"
                    return row
            metrics = miner.parse_full_period(miner.simsummary(pnl_file))
            status = miner.evaluate_and_install(job, metrics, args.timeout)
            row.update(
                {
                    "sharpe": metrics.get("sharpe"),
                    "ret_pct": metrics.get("ret_pct"),
                    "tvr_pct": metrics.get("tvr_pct"),
                    "accepted": status.get("accepted", False),
                    "reason": status.get("reason", ""),
                    "max_corr": status.get("max_corr", ""),
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["reason"] = f"exception {type(exc).__name__}: {exc}"
        return row

    rows = []
    requested_workers = len(args.candidate) if args.parallel <= 0 else args.parallel
    workers = max(1, min(requested_workers, len(args.candidate)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_candidate, item) for item in args.candidate]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{row['factor']} {row['kind']} Sharpe={row['sharpe']} ret={row['ret_pct']} "
                f"tvr={row['tvr_pct']} accepted={row['accepted']} reason={row['reason']}",
                flush=True,
            )

    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
