# baseline_v2 Candidates Backlog

Design queue only — not a v2 commitment. Each entry: rationale, expected impact (Low / Med / High), implementation effort (S / M / L), and data dependencies.

## Top 5 Candidates

### 1. Intra-period CSI 500 constituent refresh (carried from v1)
- **Rationale**: v1 freezes the universe at BT start; index inclusions/exclusions across the BT window leak survivorship and miss real-time tradeable names.
- **Impact**: High (corrects systematic bias).
- **Effort**: M (refresh universe at each rebalance; cache historical `index_weight` snapshots).
- **Data deps**: monthly snapshots of `pro.index_weight(index_code='000905.SH')` over BT window.

### 2. Suspension / limit-up / limit-down filter at execution (carried from v1)
- **Rationale**: v1 fills at next-day close even when a name is suspended or limit-locked. In A-shares, ~6% of bars hit the daily ±10% (or ±20% for ChiNext/STAR) limit; you can't buy at limit-up close or sell at limit-down close.
- **Impact**: High (removes phantom fills that inflate Sharpe).
- **Effort**: S (compare exec-day open vs prev close vs limit thresholds; mark unfillable; carry forward).
- **Data deps**: daily OHLC + listing-board info (basic-board / ChiNext 30%-limit / STAR 20%-limit) + suspended flag (`stk_suspend` or `daily.is_open`).

### 3. Sector-neutral position weighting
- **Rationale**: A-stock momentum factor is famously concentrated in 1–2 hot sectors per regime (e.g., AI in 2023, dividend in 2024, robotics 2025-Q1). A naive top-K equal weight buys only the hot sector at the wrong moment.
- **Impact**: Med-High (smoother equity curve, lower MaxDD).
- **Effort**: M (group candidates by 申万一级, cap weight per sector at e.g. 25%, redistribute to next-best per sector).
- **Data deps**: `stock_basic` industry classification (already in v1's deferred Phase 2 list).

### 4. Volatility-scaled position sizing (target portfolio vol)
- **Rationale**: Equal-weight at top_k=20 lets a single high-vol mid-cap dominate daily PnL. Inverse-vol or vol-target sizing typically improves IR more than picking better signals does.
- **Impact**: Med (improves Sharpe / IR; reduces tail).
- **Effort**: S-M (per-name 20-day std → inverse-vol weight, cap and rescale).
- **Data deps**: same as v1 (just daily close).

### 5. Cross-sectional momentum + reversal stack (avoid 1m chasing of breakouts)
- **Rationale**: A-shares show strong 1-week reversal at the cross-section despite 1–6m momentum. v1's pure breakout buys winners on Friday, then often gets clipped by Mon–Tue mean reversion. Stacking long-momentum (60d) with short-term reversal exclusion (drop top decile of last-week returns) is a documented improvement.
- **Impact**: High (addresses a known A-share microstructure artifact).
- **Effort**: M (add 5d return rank, gate top 10% out of candidates; or apply −5d as a tilt).
- **Data deps**: same as v1.

## Impact vs Effort Ranking

| Rank | Candidate | Impact | Effort | Notes |
|----:|-----------|:------:|:------:|-------|
| 1 | #2 Suspension/limit filter | High | S | Quickest win; biggest realism upgrade. |
| 2 | #5 Momentum + 1w reversal exclusion | High | M | Direct attack on A-share microstructure. |
| 3 | #3 Sector-neutral weighting | Med-High | M | Pairs naturally with #5. |
| 4 | #1 Intra-period CSI500 refresh | High | M | Bias-correction; needs historical weight snapshots. |
| 5 | #4 Vol-scaled sizing | Med | S-M | Helps IR; less dramatic than #2/#5. |

Recommended v2 ordering: tackle #2 + #5 together (one combined design note), then revisit v1 metrics; #1 + #3 in v3; #4 as a tilt overlay any time after.

## Engineering / Tooling Backlog (non-strategy)

- **MockDataFetcher drift**: synthetic series are currently `base * (1 + drift + noise).cumprod()` with positive drift. Over ~470 bars this produces NAVs ~1e12 in stress runs and defeats visual sanity checks. Switch to drift≈0, mean-reverting log-return process. Impact: Low. Effort: S. Data deps: none. Touches `src/baseline/data_fetcher.py::MockDataFetcher` only; no production strategy or fetcher impact.

## Cross-cutting Data Dependencies

- Historical CSI 500 `index_weight` snapshots (monthly) — needed for #1.
- `stk_suspend` (or per-day `daily.is_open`) — needed for #2.
- Listing-board metadata (main / ChiNext / STAR) for correct ±10/±20% limit thresholds — needed for #2.
- `stock_basic` industry — needed for #3.
- All others reuse the v1 daily-bar set.
