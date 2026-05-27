#!/usr/bin/env python3
"""Sync the installed-factor ledger from canonical Alpha* directories."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.baseline.local_factor_miner import WORK_DIR, sync_installed_factor_ledger  # noqa: E402


def installed_snapshot() -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for path in sorted(WORK_DIR.glob("Alpha*")):
        if not path.is_dir():
            continue
        latest_mtime = int(path.stat().st_mtime_ns)
        file_count = 0
        for child in path.glob("*"):
            if child.is_file():
                file_count += 1
                latest_mtime = max(latest_mtime, int(child.stat().st_mtime_ns))
        rows.append((path.name, file_count, latest_mtime))
    return tuple(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="keep syncing when installed factors change")
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args()

    rows = sync_installed_factor_ledger()
    print(f"synced installed factor ledger: {len(rows)} factors", flush=True)
    if not args.watch:
        return 0

    last = installed_snapshot()
    while True:
        time.sleep(max(args.interval, 1.0))
        current = installed_snapshot()
        if current != last:
            rows = sync_installed_factor_ledger()
            print(f"synced installed factor ledger: {len(rows)} factors", flush=True)
            last = current


if __name__ == "__main__":
    raise SystemExit(main())
