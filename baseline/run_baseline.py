"""Single formal entry: python baseline/run_baseline.py --config <yaml> [--dry-run] [--fold N].

Yaml schema:
  experiment_id: str           # e.g. v1_1_lookback20_vol1.5_topk20
  output_root: str             # default 'output/baseline_v1'
  data:
    provider: tushare | mock
    cache_root: data/tushare
    env_file: .env             # only used by tushare
    universe_code: '000905.SH' # CSI500 by default
    benchmark:   '000905.SH'
    backtest_start: 2025-01-01
    backtest_end:   '2026-05-09'
    warmup_start:   2024-07-01
  strategy:
    name: momentum_breakout
    params:
      lookback: 20
      vol_window: 20
      vol_ratio: 1.5
      mom_window: 60
      top_k: 20
      holding_period: 5
  engine:
    initial_capital: 1000000
    commission_rate: 0.0003
    slippage_bps: 5
    rebalance: W-FRI
    execution_filters:
      enabled: false
      check_suspension: true
      check_price_limits: true
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.baseline.backtest_engine import BacktestEngine, EngineConfig  # noqa: E402
from src.baseline.data_fetcher import make_fetcher  # noqa: E402
from src.baseline.metrics import assert_required, compute_metrics  # noqa: E402
from src.baseline.strategies.momentum_breakout import MomentumBreakout  # noqa: E402

log = logging.getLogger("run_baseline")

STRATEGY_REGISTRY = {
    "momentum_breakout": MomentumBreakout,
}


def _build_universe(cfg: dict, fetcher) -> list[str]:
    explicit = cfg["data"].get("explicit_universe")
    if explicit:
        return list(explicit)
    if cfg["data"].get("provider") == "mock":
        n = int(cfg["data"].get("mock_universe_size", 8))
        return [f"MOCK{i:03d}.SH" for i in range(n)]
    universe_code = cfg["data"]["universe_code"]
    df = fetcher.get_stock_list(universe_code)
    if "con_code" in df.columns:
        return df["con_code"].astype(str).tolist()
    if "ts_code" in df.columns:
        return df["ts_code"].astype(str).tolist()
    if df.index.name == "ts_code":
        return df.index.astype(str).tolist()
    raise RuntimeError("could not derive universe ts_codes from fetcher.get_stock_list output")


def _make_fetcher(cfg: dict, dry_run: bool):
    data_cfg = cfg["data"]
    provider = data_cfg["provider"]
    if provider == "mock":
        codes = data_cfg.get("explicit_universe")
        if not codes:
            size = int(data_cfg.get("mock_universe_size", 8))
            codes = [f"MOCK{i:03d}.SH" for i in range(size)]
        return make_fetcher(
            "mock",
            ts_codes=codes,
            start=data_cfg.get("warmup_start") or data_cfg["backtest_start"],
            end=data_cfg["backtest_end"],
            seed=int(data_cfg.get("seed", 0)),
        )
    return make_fetcher(
        "tushare",
        cache_root=data_cfg["cache_root"],
        env_file=data_cfg.get("env_file"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fold", type=int, default=0)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg_path = Path(args.config).resolve()
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)

    exp_id = cfg["experiment_id"]
    output_root = Path(cfg.get("output_root", "output/baseline_v1"))
    output_dir = output_root / exp_id
    log.info("[RUN-START] experiment=%s config=%s fold=%d dry_run=%s",
             exp_id, cfg_path, args.fold, args.dry_run)
    log.info("[RUN-OUT]   output_dir=%s", output_dir)

    fetcher = _make_fetcher(cfg, args.dry_run)
    universe = _build_universe(cfg, fetcher)
    log.info("[RUN-UNIV]  universe size=%d (sample=%s)", len(universe), universe[:5])

    strat_cls = STRATEGY_REGISTRY[cfg["strategy"]["name"]]
    strategy = strat_cls(**cfg["strategy"]["params"])

    engine_cfg = EngineConfig(
        start_date=cfg["data"]["backtest_start"],
        end_date=cfg["data"]["backtest_end"],
        initial_capital=cfg["engine"]["initial_capital"],
        commission_rate=cfg["engine"]["commission_rate"],
        slippage_bps=cfg["engine"]["slippage_bps"],
        benchmark=cfg["data"]["benchmark"],
        rebalance=cfg["engine"]["rebalance"],
        warmup_start=cfg["data"].get("warmup_start"),
        output_dir=str(output_dir),
        execution_filters=cfg["engine"].get("execution_filters", {}),
    )
    engine = BacktestEngine(engine_cfg, fetcher, strategy, universe)

    result = engine.run(dry_run=args.dry_run)

    if args.dry_run:
        log.info("[RUN-DRYRUN-OK] dry-run completed before main loop. extra=%s", result.extra)
        return 0

    metrics = compute_metrics(result.strat_nav, result.bench_nav, result.trades)
    assert_required(metrics)
    output_dir.mkdir(parents=True, exist_ok=True)
    engine.write_outputs(result, metrics)
    with (output_dir / "metrics.json").open("w") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
    log.info("[RUN-METRICS] %s", json.dumps(metrics, ensure_ascii=False, default=str))
    log.info("[RUN-DONE]    experiment=%s", exp_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
