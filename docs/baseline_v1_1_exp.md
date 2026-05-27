# baseline_v1 — CSI500 Mid-Cap Momentum (Volume-Price Breakout)

## Version Goal

Establish the first working baseline for the recipe/quant_strategy/ task on A-stock CSI 500 universe. Strategy direction (per leader, 2026-05-09): mid-cap momentum where price breakout is gated by elevated volume. Weekly Friday-close rebalance, equal-weight top-K, benchmark CSI 500 (cross-compare CSI 300).

This version is intentionally a clean, readable signal — no model, no leverage, no shorts. v2+ will refine.

## Strategy Specification

At each rebalance day t (Friday close):

1. For every stock in the CSI 500 universe with at least `max(lookback, vol_window, mom_window)` bars of history:
   - **breakout**: `close(t) >= max(close(t-lookback+1..t))` (i.e., today is a fresh `lookback`-day high on close).
   - **volume gate**: `vol(t) >= vol_ratio * mean(vol(t-vol_window+1..t))`.
2. Stocks passing both gates become candidates.
3. Score each candidate by `mom = close(t) / close(t - mom_window) - 1`.
4. Sort descending by `mom`, take top `top_k`, equal weight.
5. Execute on next trading day's close (engine emits trades on `cal[i+1]`) with commission 0.0003 and slippage 5 bps.
6. Hold until next Friday rebalance recomputes weights.

## Universe / Period / Benchmark

- Universe: CSI 500 constituents at start (`000905.SH`, via `index_weight`). v1 does not yet rebalance constituents intra-period; that's a known v2 candidate.
- Backtest period: **2025-01-01 to 2026-05-09**.
- Bar warmup window: **2024-07-01** to satisfy 60- and 120-day momentum/lookback windows from day one.
- Benchmark: CSI 500 (`000905.SH`). Result note will also report CSI 300 (`000300.SH`) cross-comparison.

## Experiment Configs (this version)

| ID | lookback | vol_window | vol_ratio | mom_window | top_k | holding | Notes |
|----|---------:|-----------:|----------:|-----------:|------:|--------:|-------|
| v1_1_lookback20_vol1.5_topk20 | 20 | 20 | 1.5 | 60 | 20 | 5 | central reference |
| v1_2_lookback40_vol1.5_topk20 | 40 | 20 | 1.5 | 60 | 20 | 5 | longer breakout window |
| v1_3_lookback20_vol2.0_topk20 | 20 | 20 | 2.0 | 60 | 20 | 5 | stricter volume confirmation |
| v1_4_lookback20_vol1.5_topk10 | 20 | 20 | 1.5 | 60 | 10 | 5 | concentrated portfolio |
| v1_5_lookback20_vol1.5_topk30 | 20 | 20 | 1.5 | 60 | 30 | 5 | diversified portfolio |
| v1_6_lookback60_vol1.5_topk20 | 60 | 20 | 1.5 | 120 | 20 | 5 | slow momentum variant |
| v1_7_lookback20_vol1.0_topk20 | 20 | 20 | 1.0 | 60 | 20 | 5 | volume gate disabled (ablation) |

`baseline/experiments_v1/v1_smoke_mock.yaml` is a no-network smoke config wired to `MockDataFetcher`. It is **not** part of the formal sweep (`run_experiments_v1.sh`) and is used only by the dry-run smoke test.

## Code Layout

- `src/baseline/data_fetcher.py` — `DataFetcher` ABC, `TushareDataFetcher`, `MockDataFetcher`, `make_fetcher`.
- `src/baseline/strategy_base.py` — `BaseStrategy` + `StrategyContext`.
- `src/baseline/strategies/momentum_breakout.py` — concrete strategy.
- `src/baseline/backtest_engine.py` — daily-bar engine, weekly rebalance, commission/slippage, startup banner + per-rebalance + per-month progress logs.
- `src/baseline/metrics.py` — full required metric set (Chinese keys), `compute_metrics`, `assert_required`.
- `baseline/run_baseline.py` — entry point: `--config <yaml>`, `--dry-run`, `--fold N`.
- `baseline/run_experiments_v1.sh` — single formal runner fanning out across all v1 yaml configs.
- `baseline/experiments_v1/*.yaml` — configs above.

## Observability

Every run emits (at minimum):

- a `[BT-START]` startup banner naming strategy, params, universe size, BT period, benchmark, capital, commission, slippage, output dir;
- a `[BT-CAL]` line summarizing trading-day count and rebalance count;
- one `[BT-REBAL]` line per rebalance day with picks count and current NAV;
- one `[BT-MONTH]` line per calendar month with NAV and open positions count;
- a `[BT-DONE]` line with final NAV and trade count;
- a `[RUN-METRICS]` line dumping the full metrics dict.

Dry runs exit before the main backtest loop with `[BT-DRYRUN]` lines that confirm bars rows, benchmark rows, and rebalance count.

## Required Metrics (must appear in every result)

策略收益, 策略年化收益, 超额收益, 基准收益, 阿尔法, 贝塔, 夏普比率, 信息比率, 最大回撤, 最大回撤区间, 超额收益最大回撤, 胜率, 盈亏比, 日胜率, 盈利次数, 亏损次数, 日均超额收益, 超额收益夏普比率, 策略波动率, 基准波动率.

`metrics.assert_required` enforces this before any output is written.

## Expected Behavior / Hypotheses

- v1_3 (vol_ratio=2.0) should reduce false breakouts → higher win rate, smaller portfolio churn, but lower turnover may miss some legs.
- v1_7 (vol_ratio=1.0) is an ablation that effectively disables the volume gate — expect more candidates, lower edge per name, possibly worse Sharpe.
- v1_4 vs v1_5 (top_k 10 vs 30) trades concentration risk against diversification; CSI 500 mid-caps likely favor mid-K (around 20).
- v1_6 (slow momentum) tests whether the edge is in fast or slow breakouts in 2025–2026 A-share regime.

## Known Limitations / v2 Candidates

- Static universe at BT start (no constituent rebalance). v2 should refresh CSI 500 membership at each rebalance.
- No suspension/limit-up/limit-down filter. v2 should drop names with `paused == 1` or limit-day signals at execution.
- Equal-weight only; no risk parity or vol scaling.
- No transaction-cost slippage scaling with order size or ADV.

## Dry-Run Plan (researcher-owned)

1. `python baseline/run_baseline.py --config baseline/experiments_v1/v1_smoke_mock.yaml --dry-run --fold 0` — validates module import, yaml parsing, fetcher instantiation (mock path; no network), startup banner, rebalance-date computation, and early exit before the main loop.
2. Optional secondary smoke: same config without `--dry-run` (still mock fetcher, ~80 trading days, 8 synthetic tickers) to validate engine → strategy → metrics → output writer end-to-end.
3. Tushare configs (`v1_1` … `v1_7`) require `.env` with `tushare_api_key` — held until human provides it.
