> **TEMPLATE — copy to `docs/baseline_v<N>_<M>_exp_result.md` and fill in real numbers; do not commit unfilled copies as result docs.**

# baseline_v\<N\>_\<M\>_exp_result

## Conclusion (one line)

\<one-sentence verdict — e.g. "v\<N\> 7-config sweep: best `<exp_id>` reached 信息比率 = X.XXX, but excess return remains negative; recommend Y for v\<N+1\>."\>

## Run Metadata

| Field | Value |
|---|---|
| `train_task_id` | \<train_task_id\> |
| Runner script | `baseline/run_experiments_v<N>.sh` |
| Configs | `baseline/experiments_v<N>/<config_1>.yaml`, ... |
| Workdir | `/mnt/storage/work/hwang/tinyKaggleClaw` |
| Technical focus | \<focus tags, e.g. mid-cap momentum / volume-price breakout\> |
| Benchmark(s) | CSI 500 (primary), CSI 300 (cross-comp) |
| Period | YYYY-MM-DD → YYYY-MM-DD |
| Run status | success / partial / failed |
| Exit summary | \<short note\> |
| Key log location | `output/baseline_v<N>/<exp_id>/run.log` |
| Metrics file pattern | `output/baseline_v<N>/<exp_id>/metrics.json` |
| Equity-curve dir | `output/baseline_v<N>/<exp_id>/equity_curve.csv` (or similar) |

## Key Metrics (mandatory full table)

Columns are experiment IDs. If any metric is unavailable from the backtest engine, mark the cell `N/A` with a footnote — do not silently omit rows.

| Metric | Description | \<exp_id_1\> | \<exp_id_2\> | \<exp_id_3\> | \<exp_id_4\> | \<exp_id_5\> | \<exp_id_6\> | \<exp_id_7\> |
|---|---|---|---|---|---|---|---|---|
| 策略收益 | Total strategy return |  |  |  |  |  |  |  |
| 策略年化收益 | Annualized strategy return |  |  |  |  |  |  |  |
| 超额收益 | Excess return over benchmark |  |  |  |  |  |  |  |
| 基准收益 | Benchmark return |  |  |  |  |  |  |  |
| 阿尔法 | Alpha |  |  |  |  |  |  |  |
| 贝塔 | Beta |  |  |  |  |  |  |  |
| 夏普比率 | Sharpe ratio |  |  |  |  |  |  |  |
| 胜率 | Win rate |  |  |  |  |  |  |  |
| 盈亏比 | Profit/loss ratio |  |  |  |  |  |  |  |
| 最大回撤 | Maximum drawdown |  |  |  |  |  |  |  |
| 日均超额收益 | Average daily excess return |  |  |  |  |  |  |  |
| 超额收益最大回撤 | Maximum drawdown of excess return |  |  |  |  |  |  |  |
| 超额收益夏普比率 | Sharpe ratio of excess return |  |  |  |  |  |  |  |
| 日胜率 | Daily win rate |  |  |  |  |  |  |  |
| 盈利次数 | Number of winning trades |  |  |  |  |  |  |  |
| 亏损次数 | Number of losing trades |  |  |  |  |  |  |  |
| 信息比率 | Information ratio |  |  |  |  |  |  |  |
| 策略波动率 | Strategy volatility |  |  |  |  |  |  |  |
| 基准波动率 | Benchmark volatility |  |  |  |  |  |  |  |
| 最大回撤区间 | Maximum drawdown period |  |  |  |  |  |  |  |

## Equity Curves

Equity-curve chart for this version (strategy vs benchmark):

![equity curves](baseline_v\<N\>_equity_curve.png)

- Path: `docs/baseline_v<N>_equity_curve.png`
- Coverage: \<list which experiments are drawn\>
- Notes: \<peak/trough timing, regime shifts, divergence vs benchmark\>

## CSI 300 vs CSI 500 Cross-Comparison

| Experiment | Benchmark | 策略年化收益 | 超额收益 | 信息比率 | 最大回撤 |
|---|---|---|---|---|---|
| \<exp_id_1\> | CSI 500 |  |  |  |  |
| \<exp_id_1\> | CSI 300 |  |  |  |  |
| \<exp_id_2\> | CSI 500 |  |  |  |  |
| \<exp_id_2\> | CSI 300 |  |  |  |  |

\<short interpretation: which benchmark the strategy actually beats and by how much\>

## Per-experiment Observations

- **\<exp_id_1\>**: \<what changed vs the others, what the metrics imply about the parameter choice\>
- **\<exp_id_2\>**: \<...\>
- **\<exp_id_3\>**: \<...\>
- ...

## Anomalies

- \<missing metrics, NaN values, suspicious 胜率/盈亏比 combinations, look-ahead suspects, bad fill assumptions, etc.\>

## Next-Step Suggestions

- \<concrete v<N+1> direction: parameter ranges to scan, factors to add or remove, universe tweaks, costs to model\>

## Trend Chart

After this version closes, generate:

- `docs/baseline_v<lo>_to_v<hi>_top3_trend.png` via `python baseline/plot_top3_trend.py --versions v<lo> ... v<hi> --metric '信息比率'`
- High-level takeaway: \<one sentence on direction of travel across versions\>
