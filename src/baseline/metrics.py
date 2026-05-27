"""Backtest metrics — full required Chinese metric set.

Inputs (the engine produces these):
  strat_nav: pd.Series   strategy NAV indexed by trade_date (start = 1.0)
  bench_nav: pd.Series   benchmark NAV indexed by trade_date (start = 1.0), aligned to strat_nav
  trades:    pd.DataFrame columns ts_code, side, qty, price, trade_date, pnl

Required keys (exact Chinese):
  策略收益, 策略年化收益, 超额收益, 基准收益,
  阿尔法, 贝塔, 夏普比率, 信息比率,
  最大回撤, 最大回撤区间, 超额收益最大回撤,
  胜率, 盈亏比, 日胜率, 盈利次数, 亏损次数,
  日均超额收益, 超额收益夏普比率,
  策略波动率, 基准波动率
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RF = 0.0  # risk-free; can be parameterized later


def _max_drawdown(nav: pd.Series) -> tuple[float, tuple[pd.Timestamp, pd.Timestamp] | None]:
    if nav.empty:
        return 0.0, None
    roll_max = nav.cummax()
    dd = nav / roll_max - 1.0
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    return float(dd.min()), (peak, trough)


def _ann_return(nav: pd.Series) -> float:
    if len(nav) < 2:
        return 0.0
    total = nav.iloc[-1] / nav.iloc[0] - 1.0
    years = len(nav) / TRADING_DAYS
    if years <= 0:
        return 0.0
    return float((1.0 + total) ** (1.0 / years) - 1.0)


def _ann_vol(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=0) * np.sqrt(TRADING_DAYS))


def _sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std(ddof=0) == 0:
        return 0.0
    excess = returns - RF / TRADING_DAYS
    return float(excess.mean() / returns.std(ddof=0) * np.sqrt(TRADING_DAYS))


def _alpha_beta(strat_ret: pd.Series, bench_ret: pd.Series) -> tuple[float, float]:
    if len(strat_ret) < 2 or bench_ret.var(ddof=0) == 0:
        return 0.0, 0.0
    cov = float(np.cov(strat_ret, bench_ret, ddof=0)[0, 1])
    var = float(bench_ret.var(ddof=0))
    beta = cov / var
    daily_alpha = float(strat_ret.mean() - beta * bench_ret.mean())
    return daily_alpha * TRADING_DAYS, beta


def _info_ratio(excess_ret: pd.Series) -> float:
    if excess_ret.empty or excess_ret.std(ddof=0) == 0:
        return 0.0
    return float(excess_ret.mean() / excess_ret.std(ddof=0) * np.sqrt(TRADING_DAYS))


def _win_loss(trades: pd.DataFrame) -> tuple[int, int, float, float]:
    if trades is None or trades.empty or "pnl" not in trades.columns:
        return 0, 0, 0.0, 0.0
    wins = trades.loc[trades["pnl"] > 0, "pnl"]
    losses = trades.loc[trades["pnl"] < 0, "pnl"]
    n_win = int(len(wins))
    n_loss = int(len(losses))
    total = n_win + n_loss
    win_rate = float(n_win / total) if total else 0.0
    pl_ratio = float(wins.mean() / -losses.mean()) if n_loss and n_win else 0.0
    return n_win, n_loss, win_rate, pl_ratio


def compute_metrics(
    strat_nav: pd.Series,
    bench_nav: pd.Series,
    trades: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compute the full required metric set. Always returns all keys, even at zero."""
    strat_nav = strat_nav.dropna().sort_index()
    bench_nav = bench_nav.reindex(strat_nav.index).ffill()

    strat_ret = strat_nav.pct_change().dropna()
    bench_ret = bench_nav.pct_change().reindex(strat_ret.index).fillna(0.0)
    excess_ret = strat_ret - bench_ret

    strat_total = float(strat_nav.iloc[-1] / strat_nav.iloc[0] - 1.0) if len(strat_nav) >= 2 else 0.0
    bench_total = float(bench_nav.iloc[-1] / bench_nav.iloc[0] - 1.0) if len(bench_nav) >= 2 else 0.0

    mdd, mdd_period = _max_drawdown(strat_nav)
    excess_nav = (1.0 + excess_ret).cumprod()
    excess_mdd, _ = _max_drawdown(excess_nav)

    alpha, beta = _alpha_beta(strat_ret, bench_ret)
    n_win, n_loss, win_rate, pl_ratio = _win_loss(trades)
    daily_win_rate = float((strat_ret > bench_ret).mean()) if len(strat_ret) else 0.0

    out = {
        "策略收益": strat_total,
        "策略年化收益": _ann_return(strat_nav),
        "超额收益": strat_total - bench_total,
        "基准收益": bench_total,
        "阿尔法": alpha,
        "贝塔": beta,
        "夏普比率": _sharpe(strat_ret),
        "信息比率": _info_ratio(excess_ret),
        "最大回撤": mdd,
        "最大回撤区间": (
            f"{mdd_period[0].date()} ~ {mdd_period[1].date()}" if mdd_period else ""
        ),
        "超额收益最大回撤": excess_mdd,
        "胜率": win_rate,
        "盈亏比": pl_ratio,
        "日胜率": daily_win_rate,
        "盈利次数": n_win,
        "亏损次数": n_loss,
        "日均超额收益": float(excess_ret.mean()) if len(excess_ret) else 0.0,
        "超额收益夏普比率": _sharpe(excess_ret),
        "策略波动率": _ann_vol(strat_ret),
        "基准波动率": _ann_vol(bench_ret),
    }
    return out


REQUIRED_KEYS = (
    "策略收益", "策略年化收益", "超额收益", "基准收益",
    "阿尔法", "贝塔", "夏普比率", "信息比率",
    "最大回撤", "最大回撤区间", "超额收益最大回撤",
    "胜率", "盈亏比", "日胜率", "盈利次数", "亏损次数",
    "日均超额收益", "超额收益夏普比率",
    "策略波动率", "基准波动率",
)


def assert_required(metrics: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in metrics]
    if missing:
        raise ValueError(f"missing required metric keys: {missing}")
