"""Tests for src/baseline/metrics.py.

Covers four scenarios explicitly required by leader:
  (a) flat NAV vs flat benchmark (zero strat ret, zero excess)
  (b) constant +1%/day for 252 bdays
  (c) symmetric drawdown series with deterministic peak/trough
  (d) hand-built trades for win/loss assertions

All metric assertions compare to hand-computed values with rtol=1e-6 (or strict
equality for counts and the date-string).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.baseline.metrics import REQUIRED_KEYS, assert_required, compute_metrics


def _bdays(start: str, n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def test_required_keys_count_is_20():
    assert len(REQUIRED_KEYS) == 20


def test_flat_nav_vs_flat_benchmark_a():
    idx = _bdays("2025-01-01", 60)
    strat_nav = pd.Series(1.0, index=idx)
    bench_nav = pd.Series(1.0, index=idx)
    m = compute_metrics(strat_nav, bench_nav, trades=None)
    assert_required(m)

    assert math.isclose(m["策略收益"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["基准收益"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["超额收益"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["最大回撤"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["超额收益最大回撤"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["策略波动率"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["基准波动率"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["日均超额收益"], 0.0, abs_tol=1e-12)
    assert m["盈利次数"] == 0 and m["亏损次数"] == 0
    assert math.isclose(m["胜率"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["盈亏比"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["夏普比率"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["信息比率"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["阿尔法"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["贝塔"], 0.0, abs_tol=1e-12)


def test_constant_one_percent_per_day_b():
    idx = _bdays("2025-01-01", 252)
    strat_nav = pd.Series([(1.01) ** i for i in range(252)], index=idx)
    bench_nav = pd.Series(1.0, index=idx)
    m = compute_metrics(strat_nav, bench_nav, trades=None)
    assert_required(m)

    expected_total = (1.01) ** 251 - 1.0  # 252 points => 251 daily returns
    assert math.isclose(m["策略收益"], expected_total, rel_tol=1e-9)
    assert math.isclose(m["基准收益"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["超额收益"], expected_total, rel_tol=1e-9)
    assert math.isclose(m["最大回撤"], 0.0, abs_tol=1e-12)
    assert math.isclose(m["日胜率"], 1.0, rel_tol=1e-9)
    assert math.isclose(m["日均超额收益"], 0.01, rel_tol=1e-9)
    # zero-variance returns -> sharpe is set to 0 by the implementation
    assert m["策略波动率"] >= 0.0
    assert m["基准波动率"] == 0.0


def test_symmetric_drawdown_period_c():
    # NAV: 1.0 -> 1.20 over 5 bdays, then 1.20 -> 0.96 over 5 bdays, then flat 5 bdays.
    # peak = day 5 (NAV 1.20), trough = day 10 (NAV 0.96), MDD = 0.96/1.20 - 1 = -0.20.
    idx = _bdays("2025-01-01", 16)
    up = np.linspace(1.00, 1.20, 6)         # 6 points: days 0..5
    down = np.linspace(1.20, 0.96, 6)[1:]   # 5 points: days 6..10 (skip dup 1.20)
    flat = np.full(5, 0.96)                  # 5 points: days 11..15
    nav_vals = np.concatenate([up, down, flat])
    assert nav_vals.shape[0] == 16
    strat_nav = pd.Series(nav_vals, index=idx)
    bench_nav = pd.Series(1.0, index=idx)

    m = compute_metrics(strat_nav, bench_nav, trades=None)
    assert_required(m)

    assert math.isclose(m["最大回撤"], (0.96 / 1.20) - 1.0, rel_tol=1e-9)
    peak_date = idx[5].date()
    trough_date = idx[10].date()
    assert m["最大回撤区间"] == f"{peak_date} ~ {trough_date}"


def test_trade_win_loss_metrics_d():
    idx = _bdays("2025-01-01", 8)
    strat_nav = pd.Series(1.0, index=idx)
    bench_nav = pd.Series(1.0, index=idx)
    # 3 wins (+10, +20, +5), 2 losses (-4, -6) => avg_win = 11.6667, avg_loss = -5.0
    # 胜率 = 3/5 = 0.6, 盈亏比 = 11.6667 / 5.0 = 2.33333..., 盈利次数=3, 亏损次数=2
    trades = pd.DataFrame({
        "ts_code": ["A", "B", "C", "D", "E"],
        "side":    ["sell"] * 5,
        "qty":     [-1.0] * 5,
        "price":   [10.0, 20.0, 5.0, 4.0, 6.0],
        "trade_date": idx[:5],
        "pnl":     [10.0, 20.0, 5.0, -4.0, -6.0],
    })
    m = compute_metrics(strat_nav, bench_nav, trades)
    assert_required(m)

    assert m["盈利次数"] == 3
    assert m["亏损次数"] == 2
    assert math.isclose(m["胜率"], 3 / 5, rel_tol=1e-9)
    avg_win = (10.0 + 20.0 + 5.0) / 3
    avg_loss = (-4.0 + -6.0) / 2
    assert math.isclose(m["盈亏比"], avg_win / -avg_loss, rel_tol=1e-9)


def test_assert_required_raises_on_missing_keys():
    with pytest.raises(ValueError, match="missing required metric keys"):
        assert_required({"策略收益": 0.0})  # only one key; rest missing
