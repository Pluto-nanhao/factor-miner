#!/usr/bin/env python3
"""Continuously reproduce factors.directory ideas as a separate process.

This daemon is intentionally separate from the generic factor miner. It keeps a
small coverage ledger so we can answer which factors.directory ideas were tried,
which generated runs, and which were accepted by the normal install gates.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep the replication daemon aligned with the normal miner launcher. The
# Python module reads these environment variables at import time.
os.environ.setdefault("FACTOR_MINER_CODEX_MODEL", "gpt-5.4")
os.environ.setdefault("FACTOR_MINER_DISCUSSION_MODEL", "gpt-5.4")
os.environ.setdefault("FACTOR_MINER_FEEDBACK_MODEL", "gpt-5.4")

from src.baseline import local_factor_miner as miner  # noqa: E402


OUT_DIR = ROOT / "output" / "factors_directory_replication"
STATE_PATH = OUT_DIR / "state.json"
LEDGER_PATH = OUT_DIR / "replication_ledger.csv"
FACTOR_DIR_CSV = ROOT / "output" / "factors_directory" / "factors_directory_zh.csv"
FACTOR_DIR_JSON = ROOT / "output" / "factors_directory" / "factors_directory_zh.json"
MINER_RUN_ROOT = ROOT / "output" / "factor_mining"


def replication_iters() -> str:
    return os.environ.get("FACTOR_REPLICATION_ITERS", "40")


def replication_parallel() -> str:
    return os.environ.get("FACTOR_REPLICATION_PARALLEL", "8")


def replication_codegen_parallel() -> str:
    return os.environ.get("FACTOR_REPLICATION_CODEGEN_PARALLEL", "2")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"completed_slugs": [], "attempts": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"completed_slugs": [], "attempts": {}}


def save_state(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_title(title: str, slug: str) -> str:
    title = title.replace("Factors.directorynav.menu.open", "").strip()
    return title or slug


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1] if url else ""


def load_detail_by_slug() -> dict[str, str]:
    if not FACTOR_DIR_JSON.exists():
        return {}
    try:
        pages = json.loads(FACTOR_DIR_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    details: dict[str, str] = {}
    for page in pages:
        url = page.get("url", "")
        slug = slug_from_url(url)
        if slug:
            text = re.sub(r"\n{3,}", "\n\n", page.get("text", "")).strip()
            details[slug] = text[:5000]
    return details


def load_targets() -> list[dict[str, str]]:
    if not FACTOR_DIR_CSV.exists():
        return []
    details = load_detail_by_slug()
    targets: list[dict[str, str]] = []
    with FACTOR_DIR_CSV.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            feasible = row.get("feasible_5m", "")
            if feasible not in {"yes", "partial"}:
                continue
            url = row.get("url", "")
            slug = slug_from_url(url)
            if not slug:
                continue
            targets.append(
                {
                    "slug": slug,
                    "title": clean_title(row.get("title", ""), slug),
                    "category": row.get("category", ""),
                    "feasible": feasible,
                    "url": url,
                    "text": details.get(slug, ""),
                }
            )
    return targets


def append_ledger(row: dict[str, object]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "time",
        "slug",
        "title",
        "category",
        "feasible",
        "run_dir",
        "returncode",
        "accepted",
        "best_factor",
        "best_sharpe",
        "best_ret_pct",
        "best_tvr_pct",
        "best_reason",
        "rescue_candidates",
        "rescue_returncode",
        "rescue_out",
    ]
    exists = LEDGER_PATH.exists()
    with LEDGER_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def latest_replication_run(before: set[Path]) -> Path | None:
    runs = [p for p in MINER_RUN_ROOT.glob("factor_mining_FactorsDir_*") if p not in before]
    if not runs:
        runs = list(MINER_RUN_ROOT.glob("factor_mining_FactorsDir_*"))
    return max(runs, key=lambda p: p.stat().st_mtime) if runs else None


def summarize_run(run_dir: Path | None) -> dict[str, object]:
    if run_dir is None:
        return {"accepted": False}
    result_path = run_dir / "results.csv"
    if not result_path.exists():
        return {"accepted": False}
    rows = list(csv.DictReader(result_path.open("r", encoding="utf-8", newline="")))
    if not rows:
        return {"accepted": False}

    def score(row: dict[str, str]) -> float:
        try:
            return float(row.get("sharpe") or -999)
        except ValueError:
            return -999.0

    best = max(rows, key=score)
    accepted = any(row.get("accepted") == "True" for row in rows)
    return {
        "accepted": accepted,
        "best_factor": best.get("factor", ""),
        "best_sharpe": best.get("sharpe", ""),
        "best_ret_pct": best.get("ret_pct", ""),
        "best_tvr_pct": best.get("tvr_pct", ""),
        "best_reason": best.get("reason", ""),
    }


def candidate_kind(iter_dir: Path, factor: str) -> str:
    if (iter_dir / "Config.tinyclaw.neg.xml").exists() and factor.endswith("Neg"):
        return "neg"
    if "Decay" in factor:
        suffix = factor.rsplit("Decay", 1)[-1]
        if suffix.isdigit() and (iter_dir / f"Config.tinyclaw.decay{suffix}.xml").exists():
            return f"decay{suffix}"
    if factor.endswith("Rescue") and (iter_dir / "Config.tinyclaw.rescue.xml").exists():
        return "rescue"
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
            if not factor:
                continue
            iter_dir = run_dir / f"iter_{iteration:03d}"
            if not iter_dir.exists():
                continue
            args.extend(["--candidate", f"{iter_dir}:{factor}:{candidate_kind(iter_dir, factor)}"])
    return args


def run_continuous_rescue(run_dir: Path | None) -> dict[str, object]:
    if run_dir is None:
        return {"rescue_candidates": 0, "rescue_returncode": ""}
    rescue_args = build_rescue_args(run_dir)
    if not rescue_args:
        return {"rescue_candidates": 0, "rescue_returncode": ""}
    out = run_dir / "continuous_rescue.csv"
    log(f"continuous rescue run_dir={run_dir} candidates={len(rescue_args) // 2}")
    cmd = [
        os.environ.get("GSIM_PYTHON", "/usr/local/gsim/.venv/bin/python"),
        str(ROOT / "scripts" / "rescue_specific_candidates.py"),
        "--parallel",
        os.environ.get("FACTOR_REPLICATION_RESCUE_PARALLEL", str(min(len(rescue_args) // 2, 8))),
        "--out",
        str(out),
        *rescue_args,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=int(os.environ.get("FACTOR_REPLICATION_RESCUE_TIMEOUT", "7200")))
    if proc.stdout.strip():
        log(proc.stdout.strip())
    if proc.returncode != 0:
        log(proc.stderr.strip() or f"continuous rescue failed rc={proc.returncode}")
    return {
        "rescue_candidates": len(rescue_args) // 2,
        "rescue_returncode": proc.returncode,
        "rescue_out": str(out),
    }


def configure_miner_for_target(target: dict[str, str], target_no: int) -> str:
    miner.FACTOR_LIBRARY_TARGET_SLUG = target["slug"]
    miner.FACTOR_LIBRARY_TARGET_TITLE = target["title"]
    miner.FACTOR_LIBRARY_TARGET_URL = target["url"]
    miner.FACTOR_LIBRARY_TARGET_TEXT = target.get("text", "")
    miner.FACTOR_LIBRARY_REPLICATION_BIAS = True
    miner.RESEARCH_TOPIC = f"factors_directory_replication_{target['slug']}"
    miner.CODEGEN_PARALLEL = int(replication_codegen_parallel())
    miner.DISCUSSION_PARALLEL = os.environ.get("FACTOR_REPLICATION_DISCUSSION_PARALLEL", "false").lower() not in {
        "0",
        "false",
        "no",
    }
    miner.ASYNC_FEEDBACK = False
    miner._RUN_SIGNATURES.clear()
    prefix = f"AlphaFD{target_no:04d}"
    os.environ["FACTOR_MINER_LIBRARY_TARGET_SLUG"] = target["slug"]
    os.environ["FACTOR_MINER_LIBRARY_TARGET_TITLE"] = target["title"]
    os.environ["FACTOR_MINER_LIBRARY_TARGET_URL"] = target["url"]
    os.environ["FACTOR_MINER_LIBRARY_TARGET_TEXT"] = target.get("text", "")[:5000]
    os.environ["FACTOR_MINER_LIBRARY_REPLICATION_BIAS"] = "true"
    os.environ["FACTOR_MINER_CODEGEN_PARALLEL"] = str(miner.CODEGEN_PARALLEL)
    os.environ["FACTOR_MINER_ASYNC_FEEDBACK"] = "false"
    return prefix


def run_target(target: dict[str, str], target_no: int) -> tuple[int, Path | None, dict[str, object]]:
    before = set(MINER_RUN_ROOT.glob("factor_mining_FactorsDir_*"))
    prefix = configure_miner_for_target(target, target_no)
    argv = [
        "--seed",
        f"FactorsDir_{target_no:04d}_{target['slug'][:24]}",
        "--iters",
        replication_iters(),
        "--parallel",
        replication_parallel(),
        "--timeout",
        os.environ.get("FACTOR_REPLICATION_TIMEOUT", "1200"),
        "--factor-prefix",
        prefix,
    ]
    rc = miner.main(argv)
    run_dir = latest_replication_run(before)
    summary = summarize_run(run_dir)
    return rc, run_dir, summary


def choose_next_target(targets: list[dict[str, str]], state: dict) -> tuple[int, dict[str, str]] | None:
    completed = set(state.get("completed_slugs", []))
    attempts = state.setdefault("attempts", {})
    max_attempts = int(os.environ.get("FACTOR_REPLICATION_MAX_ATTEMPTS_PER_SLUG", "1"))
    for idx, target in enumerate(targets, start=1):
        if target["slug"] in completed:
            continue
        if int(attempts.get(target["slug"], 0)) >= max_attempts:
            completed.add(target["slug"])
            state["completed_slugs"] = sorted(completed)
            continue
        return idx, target
    return None


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets()
    if not targets:
        log(f"no feasible factors.directory targets found at {FACTOR_DIR_CSV}")
        return 1
    log(f"targets={len(targets)} state={STATE_PATH}")

    once = os.environ.get("FACTOR_REPLICATION_ONCE", "false").lower() in {"1", "true", "yes"}
    sleep_s = int(os.environ.get("FACTOR_REPLICATION_SLEEP", "10"))
    state = load_state()

    while True:
        picked = choose_next_target(targets, state)
        if picked is None:
            log("all targets completed")
            save_state(state)
            return 0
        target_no, target = picked
        attempts = state.setdefault("attempts", {})
        attempts[target["slug"]] = int(attempts.get(target["slug"], 0)) + 1
        save_state(state)

        log(f"replicate {target_no}/{len(targets)} slug={target['slug']} title={target['title']}")
        try:
            rc, run_dir, summary = run_target(target, target_no)
            rescue_summary = run_continuous_rescue(run_dir)
            summary.update(rescue_summary)
        except Exception as exc:  # noqa: BLE001
            rc = 1
            run_dir = None
            summary = {"accepted": False, "best_reason": f"exception {type(exc).__name__}: {exc}"}
            log(str(summary["best_reason"]))

        row = {
            "time": dt.datetime.now().isoformat(timespec="seconds"),
            "slug": target["slug"],
            "title": target["title"],
            "category": target["category"],
            "feasible": target["feasible"],
            "run_dir": str(run_dir or ""),
            "returncode": rc,
            **summary,
        }
        append_ledger(row)
        state.setdefault("completed_slugs", []).append(target["slug"])
        state["completed_slugs"] = sorted(set(state["completed_slugs"]))
        save_state(state)
        log(
            f"done slug={target['slug']} rc={rc} accepted={summary.get('accepted')} "
            f"best={summary.get('best_factor', '')} sharpe={summary.get('best_sharpe', '')}"
        )
        if once:
            return rc
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
