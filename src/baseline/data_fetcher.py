"""Abstract data interface + tushare implementation.

Strategy and engine code MUST depend on DataFetcher, not on tushare directly.
This makes future provider migration (AKShare / Wind / local DB) a drop-in.

Tushare endpoint references (looked up via WebFetch fallback per leader approval):
- daily OHLCV:        https://tushare.pro/document/2 (pro_bar / daily)
- adj factor:         https://tushare.pro/document/2 (adj_factor)
- index daily:        https://tushare.pro/document/2 (index_daily)
- stock basic:        https://tushare.pro/document/2 (stock_basic)
- trade calendar:     https://tushare.pro/document/2 (trade_cal)
- index members:      https://tushare.pro/document/2 (index_weight)
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def calendar(self) -> Path: return self.root / "calendar"
    @property
    def basic(self) -> Path:    return self.root / "basic"
    @property
    def index(self) -> Path:    return self.root / "index"
    @property
    def adj(self) -> Path:      return self.root / "adj_factor"
    @property
    def daily(self) -> Path:    return self.root / "daily"

    def ensure(self) -> None:
        for p in (self.calendar, self.basic, self.index, self.adj, self.daily):
            p.mkdir(parents=True, exist_ok=True)


class DataFetcher(ABC):
    """Abstract data interface. All strategy / engine code depends on this."""

    @abstractmethod
    def get_trade_calendar(self, start: str, end: str) -> pd.DatetimeIndex: ...

    @abstractmethod
    def get_stock_list(self, universe: str) -> pd.DataFrame:
        """Return DataFrame indexed by ts_code with columns: name, industry, list_date."""

    @abstractmethod
    def get_daily_bars(
        self, ts_codes: Sequence[str], start: str, end: str, adjust: str = "qfq"
    ) -> pd.DataFrame:
        """Return long-format DataFrame: columns trade_date, ts_code, open, high, low, close, vol, amount.

        `adjust` in {"none", "qfq", "hfq"} for raw / forward / backward adjusted.
        Optional columns consumed by v2 execution realism filters:
        paused / is_open / trade_status / prev_close / limit_pct / board / market / is_st
        """

    @abstractmethod
    def get_index_data(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        """Return wide-format DataFrame indexed by trade_date with column close (and optional volume)."""


def _read_token_from_env_file(env_file: Path, key: str = "tushare_api_key") -> str | None:
    if not env_file.is_file():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


class TushareDataFetcher(DataFetcher):
    """Concrete tushare implementation, local-cache-first."""

    def __init__(
        self,
        cache_root: str | Path,
        token: str | None = None,
        env_file: str | Path | None = None,
    ) -> None:
        self.paths = DataPaths(Path(cache_root))
        self.paths.ensure()

        if token is None:
            token = os.environ.get("tushare_api_key") or os.environ.get("TUSHARE_TOKEN")
        if token is None and env_file is not None:
            token = _read_token_from_env_file(Path(env_file))
        self._token = token
        self._pro = None

    def _client(self):
        if self._pro is None:
            if not self._token:
                raise RuntimeError(
                    "tushare token missing; expected .env tushare_api_key or env var"
                )
            import tushare as ts  # local import; not required for unit-test code paths
            ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    @staticmethod
    def _cache_csv(path: Path) -> pd.DataFrame | None:
        if path.is_file():
            return pd.read_csv(path)
        return None

    def get_trade_calendar(self, start: str, end: str) -> pd.DatetimeIndex:
        cache = self.paths.calendar / f"trade_cal_{start}_{end}.csv"
        df = self._cache_csv(cache)
        if df is None:
            pro = self._client()
            df = pro.trade_cal(exchange="SSE", start_date=start.replace("-", ""),
                               end_date=end.replace("-", ""), is_open=1)
            df.to_csv(cache, index=False)
        return pd.to_datetime(df["cal_date"].astype(str)).sort_values().reset_index(drop=True)

    def get_stock_list(self, universe: str) -> pd.DataFrame:
        cache = self.paths.basic / f"universe_{universe}.csv"
        df = self._cache_csv(cache)
        if df is None:
            pro = self._client()
            if universe.lower() in {"csi500", "000905.sh"}:
                df = pro.index_weight(index_code="000905.SH")
            elif universe.lower() in {"csi300", "000300.sh"}:
                df = pro.index_weight(index_code="000300.SH")
            else:
                df = pro.stock_basic(list_status="L")
            df.to_csv(cache, index=False)
        return df

    def get_daily_bars(
        self, ts_codes: Sequence[str], start: str, end: str, adjust: str = "qfq"
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for code in ts_codes:
            cache = self.paths.daily / f"{code}_{start}_{end}_{adjust}.csv"
            df = self._cache_csv(cache)
            if df is None:
                import tushare as ts
                df = ts.pro_bar(
                    ts_code=code,
                    adj=None if adjust == "none" else adjust,
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                )
                if df is None or df.empty:
                    log.warning("no bars for %s in [%s, %s]", code, start, end)
                    continue
                df.to_csv(cache, index=False)
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["trade_date", "ts_code", "open", "high", "low",
                                         "close", "vol", "amount"])
        out = pd.concat(frames, ignore_index=True)
        out["trade_date"] = pd.to_datetime(out["trade_date"].astype(str))
        return out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def get_index_data(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        cache = self.paths.index / f"{index_code}_{start}_{end}.csv"
        df = self._cache_csv(cache)
        if df is None:
            pro = self._client()
            df = pro.index_daily(ts_code=index_code,
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""))
            df.to_csv(cache, index=False)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        return df.set_index("trade_date").sort_index()


class MockDataFetcher(DataFetcher):
    """In-memory synthetic fetcher used for no-network smoke tests.

    NEVER use in production configs. Routed via run_baseline.py only when the
    yaml config sets `data.provider: mock`.
    """

    def __init__(
        self,
        ts_codes: Iterable[str],
        start: str,
        end: str,
        seed: int = 0,
    ) -> None:
        import numpy as np

        self._codes = list(ts_codes)
        self._calendar = pd.bdate_range(start=start, end=end)
        rng = np.random.default_rng(seed)
        rows: list[dict] = []
        for i, code in enumerate(self._codes):
            base = 10.0 + i
            drift = 0.0005 + 0.0002 * i
            shocks = rng.normal(loc=drift, scale=0.02, size=len(self._calendar))
            close = base * (1.0 + shocks).cumprod()
            high = close * (1.0 + rng.uniform(0, 0.015, size=close.size))
            low = close * (1.0 - rng.uniform(0, 0.015, size=close.size))
            open_ = close * (1.0 + rng.normal(0, 0.005, size=close.size))
            vol = rng.uniform(1e5, 5e6, size=close.size)
            for j, dt in enumerate(self._calendar):
                rows.append({
                    "trade_date": dt, "ts_code": code,
                    "open": open_[j], "high": high[j], "low": low[j], "close": close[j],
                    "vol": vol[j], "amount": vol[j] * close[j], "paused": 0,
                    "is_open": 1, "limit_pct": 0.10, "board": "main",
                })
        self._bars = pd.DataFrame(rows)
        idx_close = self._bars.groupby("trade_date")["close"].mean()
        self._index = pd.DataFrame({"close": idx_close}).sort_index()

    def get_trade_calendar(self, start: str, end: str) -> pd.DatetimeIndex:
        mask = (self._calendar >= pd.Timestamp(start)) & (self._calendar <= pd.Timestamp(end))
        return self._calendar[mask]

    def get_stock_list(self, universe: str) -> pd.DataFrame:
        return pd.DataFrame({"ts_code": self._codes,
                             "name": [f"MOCK{i}" for i in range(len(self._codes))],
                             "industry": ["Synthetic"] * len(self._codes),
                             "list_date": ["20200101"] * len(self._codes)}).set_index("ts_code")

    def get_daily_bars(
        self, ts_codes: Sequence[str], start: str, end: str, adjust: str = "qfq"
    ) -> pd.DataFrame:
        df = self._bars
        mask = (
            df["ts_code"].isin(ts_codes)
            & (df["trade_date"] >= pd.Timestamp(start))
            & (df["trade_date"] <= pd.Timestamp(end))
        )
        return df.loc[mask].sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def get_index_data(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        idx = self._index
        return idx.loc[(idx.index >= pd.Timestamp(start)) & (idx.index <= pd.Timestamp(end))]


def make_fetcher(provider: str, **kwargs) -> DataFetcher:
    if provider == "tushare":
        return TushareDataFetcher(**kwargs)
    if provider == "mock":
        return MockDataFetcher(**kwargs)
    raise ValueError(f"unknown data provider: {provider!r}")
