#!/usr/bin/env python3
"""Rescue selected historical factor-mining iterations with the standard gates."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.baseline import local_factor_miner as miner  # noqa: E402


def parse_item(text: str) -> tuple[int, str, Path, Path, Path]:
    # Format: /path/to/iter_dir:FactorName:IterNumber
    iter_dir_s, factor_name, iteration_s = text.rsplit(":", 2)
    iter_dir = Path(iter_dir_s).resolve()
    iteration = int(iteration_s)
    return (
        iteration,
        factor_name,
        iter_dir / "Config.tinyclaw.xml",
        iter_dir / "pnl" / factor_name,
        iter_dir / "gsim.log",
    )


def iter_number(iter_dir: Path) -> int:
    match = re.search(r"iter_(\d+)", iter_dir.name)
    return int(match.group(1)) if match else 0


def variant_key(source_factor: str, variant: str) -> str:
    suffix = variant.removeprefix(source_factor)
    if suffix.startswith("Decay"):
        return "decay" + suffix.removeprefix("Decay").lower()
    if suffix == "Neg":
        return "neg"
    if suffix.endswith("RescueWide2"):
        return "rescuewide2"
    if suffix.endswith("RescueWide"):
        return "rescuewide"
    if suffix.endswith("Rescue"):
        return "rescue"
    if suffix.endswith("Decorr"):
        return "decorr"
    return suffix.lower() or "base"


def job_for_existing(iter_dir: Path, source_factor: str, variant: str) -> tuple[int, str, Path, Path, Path]:
    key = variant_key(source_factor, variant)
    xml_path = iter_dir / ("Config.tinyclaw.xml" if key == "base" else f"Config.tinyclaw.{key}.xml")
    log_path = iter_dir / ("gsim.log" if key == "base" else f"gsim.{key}.log")
    pnl_dir = iter_dir / "pnl" if key == "base" else iter_dir / f"pnl_{key}"
    return iter_number(iter_dir), variant, xml_path, pnl_dir / variant, log_path


def parse_existing_variants(action: str) -> list[str]:
    marker = "已有救援/取负/decay产物但未入库:"
    if marker not in action:
        return []
    return [part.strip() for part in action.split(marker, 1)[1].split(",") if part.strip()]


def auto_tasks(report: Path) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    seen: set[tuple[str, str, Path]] = set()
    with report.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            status = row.get("status", "")
            source_factor = row["factor"]
            log_path = Path(row["log"]).resolve()
            iter_dir = log_path.parent if log_path.parent.name.startswith("iter_") else log_path.parent / f"iter_{int(row['iter']):03d}"
            labels = row.get("labels", "")
            if status == "handled_active_variant":
                continue
            if status == "todo_negate":
                key = ("negate", source_factor, iter_dir)
                if key not in seen:
                    seen.add(key)
                    tasks.append({"mode": "negate", "source_factor": source_factor, "job": parse_item(f"{iter_dir}:{source_factor}:{row['iter']}")})
            for variant in parse_existing_variants(row.get("action", "")):
                variant_path = iter_dir / f"{variant}.py"
                job = job_for_existing(iter_dir, source_factor, variant)
                if variant_path.exists() and job[2].exists():
                    key = ("existing", variant, iter_dir)
                    if key not in seen:
                        seen.add(key)
                        tasks.append({"mode": "existing", "source_factor": source_factor, "job": job})
            if status == "todo_decay" or "high_tvr_decay" in labels:
                base_job = parse_item(f"{iter_dir}:{source_factor}:{row['iter']}")
                for days in miner.DECAY_RETRY_DAYS:
                    key = (f"decay{days}", source_factor, iter_dir)
                    if key not in seen:
                        seen.add(key)
                        tasks.append({"mode": "decay", "source_factor": source_factor, "days": days, "job": base_job})
    return tasks


def write_outputs(rows: list[dict[str, object]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "manual_rescue_results.csv"
    md_path = out_dir / "manual_rescue_results.md"
    fields = [
        "source_factor",
        "rescued_factor",
        "iter_dir",
        "sharpe",
        "ret_pct",
        "tvr_pct",
        "accepted",
        "reason",
        "max_corr",
        "mode",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Manual Rescue Results",
        "",
        "| mode | source | rescued | Sharpe | ret% | tvr% | accepted | max_corr | reason |",
        "|---|---|---:|---:|---:|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {mode} | {source_factor} | {rescued_factor} | {sharpe} | {ret_pct} | {tvr_pct} | "
            "{accepted} | {max_corr} | {reason} |".format(**row)
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--negate", action="append", default=[], help="iter_dir:FactorName:IterNumber")
    parser.add_argument("--auto-report", type=Path)
    parser.add_argument("--parallel", type=int, default=8, help="worker count; <=0 explicitly means all candidates")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "output" / "factor_mining" / "manual_rescue")
    args = parser.parse_args()

    def run_backtest_task(task: dict[str, object]) -> tuple[dict[str, object], tuple[int, str, Path, Path, Path] | None, dict | None]:
        job = task["job"]
        iteration, factor_name, _xml, _pnl, log_path = job
        source_factor = str(task.get("source_factor", factor_name))
        row: dict[str, object] = {
            "source_factor": source_factor,
            "rescued_factor": "",
            "iter_dir": str(log_path.parent),
            "sharpe": "",
            "ret_pct": "",
            "tvr_pct": "",
            "accepted": False,
            "reason": "",
            "max_corr": "",
            "mode": task.get("mode", ""),
        }
        try:
            mode = task.get("mode")
            if mode == "negate":
                test_job = miner.make_negated_job(job)
            elif mode == "decay":
                test_job = miner.make_decay_job(job, int(task["days"]))
            else:
                test_job = job
            _it, test_name, test_xml, test_pnl, test_log = test_job
            rc = miner.run_gsim(test_xml, test_log, args.timeout)
            row["rescued_factor"] = test_name
            if rc != 0:
                row["reason"] = f"run_failed rc={rc} log={test_log}"
                return row, None, None
            metrics = miner.parse_full_period(miner.simsummary(test_pnl))
            row.update(
                {
                    "sharpe": metrics.get("sharpe"),
                    "ret_pct": metrics.get("ret_pct"),
                    "tvr_pct": metrics.get("tvr_pct"),
                }
            )
            return row, test_job, metrics
        except Exception as exc:  # noqa: BLE001
            row["reason"] = f"exception {type(exc).__name__}: {exc}"
            return row, None, None

    tasks: list[dict[str, object]] = []
    tasks.extend({"mode": "negate", "source_factor": parse_item(item)[1], "job": parse_item(item)} for item in args.negate)
    if args.auto_report:
        tasks.extend(auto_tasks(args.auto_report))

    rows: list[dict[str, object]] = []
    completed: list[tuple[dict[str, object], tuple[int, str, Path, Path, Path] | None, dict | None]] = []
    requested_workers = len(tasks) if args.parallel <= 0 else args.parallel
    workers = max(1, min(requested_workers, len(tasks) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_backtest_task, task) for task in tasks]
        for future in as_completed(futures):
            row, neg_job, metrics = future.result()
            completed.append((row, neg_job, metrics))
            rows.append(row)
            write_outputs(rows, args.out_dir)
            print(
                f"backtest_done {row['source_factor']} -> {row['rescued_factor']} "
                f"Sharpe={row['sharpe']} ret={row['ret_pct']} tvr={row['tvr_pct']} "
                f"reason={row['reason']}",
                flush=True,
            )

    rows = []
    for row, neg_job, metrics in completed:
        if neg_job is not None and metrics is not None:
            try:
                status = miner.evaluate_and_install(neg_job, metrics, args.timeout)
                row.update(
                    {
                        "accepted": status.get("accepted", False),
                        "reason": status.get("reason", ""),
                        "max_corr": status.get("max_corr", ""),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                row["reason"] = f"install_exception {type(exc).__name__}: {exc}"
        rows.append(row)
        write_outputs(rows, args.out_dir)
        print(
            f"install_checked {row['source_factor']} -> {row['rescued_factor']} "
            f"Sharpe={row['sharpe']} ret={row['ret_pct']} tvr={row['tvr_pct']} "
            f"accepted={row['accepted']} reason={row['reason']}",
            flush=True,
        )

    write_outputs(rows, args.out_dir)
    print(f"wrote {args.out_dir / 'manual_rescue_results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
