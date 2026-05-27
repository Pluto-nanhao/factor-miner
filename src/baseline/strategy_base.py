"""Strategy base class. Strategies own only signal/position logic, not execution."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class StrategyContext:
    """Per-rebalance context handed to strategies by the engine."""
    rebalance_date: pd.Timestamp
    universe: list[str]
    bars: pd.DataFrame
    params: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """All concrete strategies inherit this and depend ONLY on the abstract DataFetcher.

    Two contracts:
      generate_signals(ctx) -> ranked candidates with optional score
      get_positions(signals, ctx) -> target weights (sum <= 1.0)
    """

    def __init__(self, **params: Any) -> None:
        self.params = dict(params)

    @abstractmethod
    def generate_signals(self, ctx: StrategyContext) -> pd.DataFrame:
        """Return DataFrame indexed by ts_code with at least column `score`."""

    def get_positions(self, signals: pd.DataFrame, ctx: StrategyContext) -> pd.Series:
        """Default: equal-weight top-K by `score`. Override for sizing logic."""
        top_k = int(self.params.get("top_k", 20))
        if signals.empty:
            return pd.Series(dtype=float)
        picks = signals.sort_values("score", ascending=False).head(top_k)
        if picks.empty:
            return pd.Series(dtype=float)
        weight = 1.0 / len(picks)
        return pd.Series(weight, index=picks.index)
