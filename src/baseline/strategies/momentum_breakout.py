"""Volume-price breakout momentum strategy for baseline_v1.

Signal definition (per leader directive):
  - candidate stock at rebalance day t if close(t) is the max of close over the past
    `lookback` trading days
  - AND volume(t) >= `vol_ratio` * mean(volume) over the past `vol_window` days
  - rank candidates by recent return strength (close(t) / close(t - mom_window) - 1)

Position sizing: equal-weight top-K (default 20).
Holding: weekly Friday rebalance at engine level; `holding_period` is informational and
used by extended variants (e.g. min-hold filtering).
"""
from __future__ import annotations

import math

import pandas as pd

from ..strategy_base import BaseStrategy, StrategyContext


class MomentumBreakout(BaseStrategy):
    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        lookback = int(self.params.get("lookback", 20))
        vol_window = int(self.params.get("vol_window", 20))
        vol_ratio = float(self.params.get("vol_ratio", 1.5))
        mom_window = int(self.params.get("mom_window", 60))
        reversal_window = int(self.params.get("reversal_window", 5))
        reversal_exclude_top_pct = float(self.params.get("reversal_exclude_top_pct", 0.0))

        bars = ctx.bars
        if bars.empty:
            return pd.DataFrame(columns=["score"])

        end = ctx.rebalance_date
        recent = bars[bars["trade_date"] <= end].copy()
        rows: list[dict] = []
        for code, g in recent.groupby("ts_code"):
            g = g.sort_values("trade_date")
            if len(g) < max(lookback, vol_window, mom_window, reversal_window):
                continue
            close = g["close"].to_numpy()
            vol = g["vol"].to_numpy()
            close_t = close[-1]
            high_n = close[-lookback:].max()
            vol_t = vol[-1]
            vol_ma = vol[-vol_window:].mean()
            if vol_ma <= 0:
                continue
            is_breakout = close_t >= high_n - 1e-9
            vol_ok = vol_t >= vol_ratio * vol_ma
            if not (is_breakout and vol_ok):
                continue
            mom = close_t / close[-mom_window] - 1.0
            reversal_5d = close_t / close[-reversal_window] - 1.0
            rows.append({
                "ts_code": code,
                "score": float(mom),
                "reversal_5d": float(reversal_5d),
            })

        if not rows:
            return pd.DataFrame(columns=["score"])
        signals = pd.DataFrame(rows).set_index("ts_code")

        if reversal_exclude_top_pct > 0 and len(signals) > 1:
            n_exclude = math.ceil(len(signals) * reversal_exclude_top_pct)
            n_exclude = max(1, n_exclude)
            n_exclude = min(len(signals) - 1, n_exclude)
            exclude_idx = signals["reversal_5d"].nlargest(n_exclude).index
            signals = signals.drop(index=exclude_idx)

        return signals
