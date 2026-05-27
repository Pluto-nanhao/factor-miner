"""Tests for src/baseline/backtest_engine.py end-to-end on the no-network MockDataFetcher."""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from src.baseline.backtest_engine import BacktestEngine, EngineConfig
from src.baseline.data_fetcher import MockDataFetcher
from src.baseline.metrics import REQUIRED_KEYS, compute_metrics
from src.baseline.strategy_base import BaseStrategy, StrategyContext
from src.baseline.strategies.momentum_breakout import MomentumBreakout


def _make_engine(tmp_path):
    codes = [f"MOCK{i:03d}.SH" for i in range(10)]
    fetcher = MockDataFetcher(codes, start="2024-09-01", end="2025-04-30", seed=7)
    cfg = EngineConfig(
        start_date="2025-01-01",
        end_date="2025-03-31",  # ~60 trading days
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
        slippage_bps=5.0,
        benchmark="000905.SH",
        rebalance="W-FRI",
        warmup_start="2024-09-01",
        output_dir=str(tmp_path / "out"),
    )
    strat = MomentumBreakout(lookback=20, vol_window=20, vol_ratio=1.2,
                             mom_window=30, top_k=3, holding_period=5)
    return BacktestEngine(cfg, fetcher, strat, codes)


def test_dry_run_exits_before_main_loop(tmp_path, caplog):
    engine = _make_engine(tmp_path)
    with caplog.at_level(logging.INFO, logger="src.baseline.backtest_engine"):
        result = engine.run(dry_run=True)
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BT-START]") for m in msgs)
    assert any(m.startswith("[BT-CAL]") for m in msgs)
    assert any(m.startswith("[BT-DRYRUN]") for m in msgs)
    # dry-run path must NOT emit per-rebalance lines
    assert not any(m.startswith("[BT-REBAL]") for m in msgs)
    assert result.extra.get("dry_run") is True
    assert result.extra.get("rebalance_days", 0) > 0


def test_full_run_emits_observability_logs(tmp_path, caplog):
    engine = _make_engine(tmp_path)
    with caplog.at_level(logging.INFO, logger="src.baseline.backtest_engine"):
        result = engine.run(dry_run=False)
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BT-START]") for m in msgs)
    assert any(m.startswith("[BT-CAL]") for m in msgs)
    assert sum(m.startswith("[BT-REBAL]") for m in msgs) >= 1
    # At least one [BT-MONTH] line per calendar month covered (Jan/Feb/Mar 2025).
    months = {m.split()[1] for m in msgs if m.startswith("[BT-MONTH]")}
    assert {"2025-01", "2025-02", "2025-03"}.issubset(months)
    assert any(m.startswith("[BT-DONE]") for m in msgs)

    assert isinstance(result.strat_nav, pd.Series)
    assert isinstance(result.bench_nav, pd.Series)
    assert len(result.strat_nav) > 0
    assert result.strat_nav.index.equals(result.bench_nav.index)


def test_full_run_metrics_have_all_required_keys(tmp_path):
    engine = _make_engine(tmp_path)
    result = engine.run(dry_run=False)
    metrics = compute_metrics(result.strat_nav, result.bench_nav, result.trades)
    for key in REQUIRED_KEYS:
        assert key in metrics, f"missing required metric: {key}"


def test_unsupported_rebalance_raises(tmp_path):
    engine = _make_engine(tmp_path)
    engine.cfg = EngineConfig(**{**engine.cfg.__dict__, "rebalance": "BOGUS"})
    with pytest.raises(ValueError, match="unsupported rebalance rule"):
        engine.run(dry_run=False)


class _FixedWeightStrategy(BaseStrategy):
    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        return pd.DataFrame({"score": [1.0]}, index=["MOCK000.SH"])

    def get_positions(self, signals: pd.DataFrame, ctx: StrategyContext) -> pd.Series:
        return pd.Series({"MOCK000.SH": 1.0})


def test_execution_filter_blocks_limit_up_buy(tmp_path, caplog):
    codes = ["MOCK000.SH", "MOCK001.SH"]
    fetcher = MockDataFetcher(codes, start="2025-01-01", end="2025-01-10", seed=3)
    fetcher._bars = fetcher._bars.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    fetcher._bars["prev_close"] = fetcher._bars.groupby("ts_code")["close"].shift(1)
    fetcher._bars["paused"] = 0
    fetcher._bars["is_open"] = 1
    fetcher._bars["limit_pct"] = 0.10
    exec_day = pd.Timestamp("2025-01-06")
    mask = fetcher._bars["trade_date"] == exec_day
    fetcher._bars.loc[mask & (fetcher._bars["ts_code"] == "MOCK000.SH"), "close"] = (
        fetcher._bars.loc[mask & (fetcher._bars["ts_code"] == "MOCK000.SH"), "prev_close"] * 1.10
    )

    cfg = EngineConfig(
        start_date="2025-01-01",
        end_date="2025-01-10",
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
        slippage_bps=5.0,
        benchmark="000905.SH",
        rebalance="W-FRI",
        warmup_start="2025-01-01",
        output_dir=str(tmp_path / "out"),
        execution_filters={
            "enabled": True,
            "check_suspension": True,
            "check_price_limits": True,
        },
    )
    strategy = _FixedWeightStrategy()
    engine = BacktestEngine(cfg, fetcher, strategy, codes)

    with caplog.at_level(logging.INFO, logger="src.baseline.backtest_engine"):
        result = engine.run(dry_run=False)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BT-EXEC-BLOCK] buy MOCK000.SH") for m in msgs)
    assert any(m.startswith("[BT-EXEC-SUMMARY] 2025-01-06 blocked buys=1 sells=0") for m in msgs)
    assert result.trades.empty
