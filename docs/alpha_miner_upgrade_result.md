# alpha_miner Upgrade Result

**Date**: 2026-05-09
**Author**: trainer (research_mvp runtime)
**Source data**: `tinyKaggleClaw/output/alpha_miner_runs/{baseline,upgraded_v1_partial,upgraded_v2_hung,upgraded_v3,upgraded_v4}_run_log.json`

## TL;DR

The upgrade bundle (#1 + #2 + #5 + #B + rescue + thinking-disabled + negate-retry) is **structurally working** but did **not** lift the accept count: 0 accepts across baseline (40 iters) + v1_partial (13) + v2_hung (1) + v3 (16) + v4 (16) = 86 iterations total. F1 (description_zh fill) and F2 (`骨架 / 替换维度` markers) regressions are **resolved** in v3 and v4. **Rescue and negate-retry are FUNCTIONAL**, not inert: each fired correctly once in v4 (rescue iter 4: weak_sharpe gate → LLM micro-edit → full re-backtest, end-to-end pipeline executed for the first time in production; negate-retry iter 8: original Sharpe<0 → post-flip Sharpe=2.25 ret=21.65%). Both gate-failed downstream, but the validation of ~225 LOC of new code (rescue.py + `__main__.py` integration + NEGATE_RETRY_*_MARGIN constants) under real concurrency + dmxapi + thinking-disabled is itself a v4 win independent of the accept count. The peak Sharpe trajectory is **baseline 3.31 → v3 4.19 → v4 2.56**, suggesting v4 produced fewer high-Sharpe candidates than v3, even though it produced more candidates *near* the rescue gate (2 entries in `[2.5, 3.0)` vs 0 in v3). The key v5 finding is that rescue's micro-edit **regressed** iter 4 from Sharpe=2.56 to 2.04 — the rescue prompt is letting the LLM swap too many axes at once. The remaining bottleneck is **upstream of rescue**: the LLM Sharpe distribution is still centered well below the 3.0 gate, so even a working rescue pathway cannot manufacture accepts at the current borderline rate.

## Caveats / Confounds

| Confound | Detail |
|---|---|
| +1/+2/+3 phase shift on direction-rotation start | Banner directions: baseline=`[0]` 价格位置与VWAP, v1=`[1]` 开盘冲击与消化, v2=`[1]`, v3=`[1]`, v4=`[3]` 日内收益序列的自相关与微观结构. Each shift is the consequence of `__main__.py`'s legacy `get_next_direction()` banner call advancing the shared `prompt_direction_idx.txt` before `_resolve_per_iter_direction()` reads it. Per-iter rotation visits all 8 directions across 16 iters anyway, so dimension breakdowns are still comparable. |
| `failure_memory.jsonl` is shared global state and accumulates across runs | Running counts (post-each-event):  baseline_start ≈ 247 (leader-cited from researcher's earlier read; not directly captured because trainer was bootstrapped after baseline started); v1_start = 282; v1_kill / v2_start = 300; v2_hung_kill / v3_start = 301; v3_end / v4_start = 317; v4_end = 333. Each run's accept/failure counts in this doc come from that run's own `<run_dir>/run_log.json`, never from `failure_memory.jsonl`. |
| baseline finished naturally; no kill needed | PID 3582924 ran 40/40 iters to completion between 12:28:05 and 13:22:17 (~54 min). Used direction `[0]` for the run-startup banner; per-iter rotation was NOT enabled in baseline (lever #B was not yet applied). |
| v1 killed at iter 13 | F1 (empty description_zh) + F3 (Amo* monoculture) both confirmed in v1_partial; killed before 16 iters per leader directive. Archive: `upgraded_v1_partial_run_log.json` (13 entries). |
| v2 first attempt hung 7m24s on iter 1 LLM call | Prefill-mechanic + dmxapi-proxy incompatibility. After logging `LLM 生成失败: API 响应中无 TextBlock`, the parent process *also died* when starting iter 2 — i.e. the prefill incompat caused parent exit, not just per-iter failure. v3's prefill removal alone is the fix. Archive: `upgraded_v2_hung_run_log.json` (1 entry). |
| v4 first attempt hung 2m25s on iter 1 LLM call | Same symptom as v2 even though v4 had thinking-disabled + rescue + negate-retry; root cause was the `env -i` launch invocation stripping a TLS/locale environment variable the anthropic SDK needs (likely `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / locale-related). Empirically confirmed by the relaunch: identical source code, same idx, plain `nohup` (full inherited env) → `[iter 1] 因子名:` landed at +31s. Recommendation: do NOT use `env -i` for real-API alpha_miner launches; the smoke test was case A (single-call + full-env) which did not exercise the env-isolated + concurrent-call surface. |
| Rescue tracking heuristic correction | The rescue/negate-retry traces are NOT recorded as separate `run_log.json` rows or via a `rescued_from` / `rescue_reason` field on the original entry. They are only visible in the launch log (`upgraded_v4.log`). Negate-retry overwrites the original entry's Sharpe in place; rescue produces an `_v2`-suffixed micro-edited factor that is evaluated standalone and whose result is not appended to `run_log.json`. Trainer's run_log-only first-pass scan therefore reported `0 rescue triggers` — corrected by re-reading the launch log. Recommend a follow-up patch to make rescue/negate outcomes first-class rows in `run_log.json` so trainers can audit without log scraping. |
| Backups available for revert | Researcher's pre-upgrade backups exist at `generator.py.bak.20260509_050550` (pre-v1), `generator.py.bak.v2.20260509_054244` (pre-v2-patch), `generator.py.bak.v4.20260509_061710`, `__main__.py.bak.v4.20260509_061710`, `_smoke_test_api.py.bak.v4.20260509_061710`. |

## 5-Dimension Comparison

### Dimension 1 — Accept count and rate

| Run | Iters | Accepts | Accept rate | Wall-clock |
|---|---|---|---|---|
| baseline | 40 | 0 | 0.0% | 12:28:05 → 13:22:17 (~54 min) |
| v1_partial | 13 | 0 | 0.0% | 13:23:49 → 13:38:04 (~14 min, killed at iter 13) |
| v2_hung | 1 | 0 | 0.0% | 13:47:13 → 13:54:47 (~7 min iter-1 hang, then parent exit) |
| v3_no_prefill | 16 | 0 | 0.0% | 14:01:06 → 14:26:14 (~25 min) |
| v4_full | 16 | 0 | 0.0% | 15:14:21 → 15:31:17 (~17 min, plus rescue tail to 15:46:56) |

### Dimension 2 — Failure-category breakdown (per run)

| Category | baseline | v1_partial | v2_hung | v3 | v4 |
|---|---|---|---|---|---|
| accepted | 0 | 0 | 0 | 0 | 0 |
| runtime_error | 9 (22.5%) | 10 (76.9%) | 0 | 5 (31.3%) | 5 (31.3%) |
| low_sharpe_and_ret | 30 (75.0%) | 2 (15.4%) | 0 | 10 (62.5%) | 11 (68.8%) |
| high_corr | 1 (2.5%) | 0 | 0 | 1 (6.3%) | 0 |
| llm_gen_error | 0 | 1 (7.7%) | 1 (100%) | 0 | 0 |
| other | 0 | 0 | 0 | 0 | 0 |

Notes: v1_partial's runtime_error rate of 77% is dramatically higher than baseline's 22.5% and v3/v4's 31.3% — direct evidence that lever #5's prefill mechanic was breaking generated code on top of the description_zh empty-rate issue. v3 and v4 both stabilize around 31% runtime_error, suggesting that bucket is now driven by intrinsic LLM code quality on amount-related directions, not by the prefill mechanic.

### Dimension 3 — Sharpe distribution among parseable failures

| Stat | baseline | v1_partial | v3 | v4 |
|---|---|---|---|---|
| n parseable | 31 | 2 | 11 | 11 |
| min | -2.84 | -0.07 | -2.82 | -1.76 |
| median | 1.37 | 0.70 | 0.49 | 1.87 |
| mean | 0.64 | 0.70 | 0.12 | 0.89 |
| max | **3.31** | 1.48 | **4.19** | 2.56 |
| n in [2.5, 3.0) (rescue weak_sharpe window) | 2 | 0 | 0 | **2** |
| n ≥ 3.0 | 3 | 0 | 1 | 0 |

Notable: v4's median jumps to 1.87 (vs v3's 0.49 and baseline's 1.37) — the LLM Sharpe distribution shifted toward, but did not cross, the gate. v4's max=2.56 is below baseline (3.31) and v3 (4.19). v3's iter-3 Sharpe=4.19 (rejected on `max_corr=0.725`) is the single best Sharpe across the entire experiment and would have triggered `high_corr` rescue in v4. v4's two `[2.5, 3.0)` entries (iter 4 = 2.56, iter 16 = 2.50) are the closest near-misses and are the basis for the rescue subsystem activity below.

### Dimension 4 — Direction coverage (per-iter rotation)

Lever #B (per-iter direction rotation) was NOT in baseline; baseline ran on direction `[0]` for all 40 iters (no rotation).

In v1, v3, v4 the per-iter rotation is enabled. With 16 iters across 8 directions the expected per-direction count is 2.0; the actual coverage cannot be precisely reconstructed from the run_log alone (run_log does not record per-iter direction id), but the factor-name diversity below is a reasonable proxy:

| Run | Amo* / total | Non-Amo factor names | Diverse-factor rate |
|---|---|---|---|
| baseline (no rotation) | 36/40 (90%) | 4 | 10% |
| v1_partial | 12/13 (92%) | 1 | 8% |
| v3 | 10/16 (62%) | 6 | 38% |
| v4 | 14/16 (88%) | 2 | 12% |

v3 had the strongest non-Amo coverage (`AlphaHwangClosePriceRangePosition`, `AlphaHwangCondAccelAsym`, `AlphaHwangIntraBarCVaRAsym`, …). v4's banner direction was `[3]` 日内收益序列的自相关与微观结构, but the resulting factor names regressed back to ~88% Amo*. This suggests the per-iter rotation reaches non-Amo directions in v3 but the LLM in v4 (with rescue + thinking-disabled + negate-retry restored) generated more Amo-themed candidates — possibly because the larger `failure_memory.jsonl` (317 entries at v4 start vs 282 at v1 start) is biasing the prompt toward already-seen Amo factor patterns. F3 is **partially resolved** (v3) but **regressed** (v4); root cause unconfirmed.

### Dimension 5 — description_zh fill + marker presence (F1 + F2)

| Run | filled / total | filled-conditional-on-success | contains 骨架 | contains 替换维度 |
|---|---|---|---|---|
| baseline | 40/40 (100%) | 100% | 0/40 | 0/40 |
| v1_partial | 2/13 (15%) | 2/2 (100%) | 0/13 | 0/13 |
| v2_hung | 0/1 (0%) | n/a | 0 | 0 |
| v3 | 13/16 (81%) | 13/13 (100%) | 13/16 (81%) | 13/16 (81%) |
| v4 | 12/16 (75%) | 12/12 (100%) | 12/16 (75%) | 12/16 (75%) |

In v3 and v4 every empty description_zh coincides exactly with a `runtime_error` (i.e. a generation that did not parse cleanly); description_zh is **always filled when generation succeeds**. F1 verdict: **resolved** by removing the prefill mechanic (v3 evidence; v4 confirms). The schema-line F2 nudge was *not* the load-bearing fix for fill rate — the prefill removal alone restored it. F2 marker uptake is **resolved** in both v3 and v4 (12/13 of v3 filled descriptions and 12/12 of v4 filled descriptions contain both `骨架` and `替换维度`).

### v4-specific dimensions: rescue subsystem and negate-retry

| Subsystem | Triggered | Trigger detail | Outcome | Outcome detail |
|---|---|---|---|---|
| rescue (weak_sharpe) | 1× | iter 4: original Sharpe=2.56 ret=21.98%; in `[2.5, 3.0)` AND \|ret\|≥15 → gate satisfied | failed | rescue produced micro-edit `AlphaHwangAmoRatioSkew60D_v2`; re-backtest Sharpe=2.04 ret=19.35% (regressed below the 2.5 lower bound and missed the 20% ret gate). |
| rescue (weak_ret) | 0× | no v4 entry had Sharpe ≥ 3.0 | n/a | — |
| rescue (high_corr) | 0× | no v4 entry had Sharpe ≥ 3.0 AND ret ≥ 20% AND max_corr ∈ [0.7, 0.85) | n/a (counterfactual: v3 iter 3 Sharpe=4.19 max_corr=0.725 would have triggered high_corr if v3 had had rescue active) | — |
| negate-retry | 1× | iter 8: original Sharpe was negative with \|Sharpe\| above the negate-retry gate | clean re-eval, gate-fail | post-negate Sharpe=2.25 ret=21.65%; replaced original metrics in run_log in place. |
| thinking-disabled | n/a (validation) | — | passed | 16/16 v4 iters produced non-empty `python_code` AND non-empty `factor_name`; no dropped/empty responses. |

Total v4 LLM activity counted from the launch log: 16 main-loop generations + 1 rescue micro-edit = 17 LLM calls; all returned parseable text blocks. Hypothesis (b) (env-stripping) was the root cause of the v4 first-attempt hang; thinking-disabled + restored prefill is compatible with dmxapi when env is fully inherited.

## Recommendations for v5

Concrete, actionable for a researcher to implement directly without re-doing the analysis:

1. **Closed by v4** — F1 (description_zh fill rate). Prefill removal is the load-bearing fix. The F2 schema-line update is independently valuable for marker uptake but is not what restored fill rate.
2. **Closed by v4** — F2 (`骨架 / 替换维度` marker uptake). Both v3 and v4 hit ~100% conditional on a successful generation.
3. **Open** — F3 (Amo* saturation). v3 reached 62% Amo* (38% diverse); v4 regressed to 88% Amo*. The likely culprits, in priority order:
   - Growing `failure_memory.jsonl` (317 → 333) biases the prompt toward already-seen Amo patterns. Suggested experiment: prune or re-rank `failure_memory.jsonl` so non-Amo entries are over-sampled in the prompt context, OR cap the per-prompt failure-memory injection size by direction.
   - The per-iter rotation prompt structure may not be strict enough about non-Amo directions when the most-prominent ingested factor (`AmoDiffAutocorr`) has a complex skeleton. Suggested experiment: when the resolved direction is non-Amo, inject an **explicit ban** on factor names starting with `AlphaHwangAmo*` for that iter.
4. **Open** — wide-Sharpe-distribution gap. The rescue subsystem is wired correctly but is only ever going to manufacture accepts when the LLM produces near-misses. v4 produced 2 near-misses out of 16 (12.5%), v3 produced 1 out of 16 (6%); even a 100%-success rescue pathway only yields ~1-2 accepts/run at this rate. Suggested experiments, in priority order:
   - **Factor-variant prompt coupling**: when the LLM picks a baseline-skeleton, attach 2-3 successful-variant code snippets from `FACTOR_INDEX.md` as in-context exemplars (lever #7 from researcher's `failure_analysis.md`, still open).
   - **Saturation-weighted sampling** (lever #3, still open): down-weight directions that have already produced ≥3 same-prefix factors in the recent window; up-weight directions with no factors yet.
   - **Rescue prompt tightening — single-axis swap per reason** (key v4-derived finding): v4's iter-4 rescue regressed Sharpe from 2.56 to 2.04, indicating the LLM swapped too many axes at once. Replace the rescue prompt's menu-style swap-axis offer with an explicit **single-axis** mapping selected by the trigger reason:
     - `weak_sharpe` (Sharpe in [2.5, 3.0), |ret|≥15%) → ONLY shift the lookback window (e.g. 20→40, 40→60); preserve aggregator, source data, and decay.
     - `weak_ret` (Sharpe ≥ 3.0, ret in [15%, 20%)) → ONLY sharpen the cross-section step (e.g. switch demean → rank, or rank → z-score truncate); preserve lookback and aggregator.
     - `high_corr` (Sharpe ≥ 3.0, ret ≥ 20%, max_corr in [0.7, 0.85)) → ONLY swap the source data axis (e.g. amount → return, or 5m bars → 1m bars); preserve aggregator and lookback.
     Plus a hard diff-budget instruction in the rescue prompt: "change ≤15 lines from the original; preserve all helper functions and class structure."
5. **Open** — Amo*-prefix runtime-error concentration. In v3, 3 of 5 runtime_errors hit Amo* iters and 0 of the 6 non-Amo iters errored. In v4, 5 of 5 runtime_errors are Amo*. This is a real signal: the LLM's Amo-direction code is more fragile than its non-Amo code, possibly because `AmoDiffAutocorr`'s skeleton is the most complex and harder to extend safely. Suggested experiment: add a static-analysis pre-flight step that runs `ast.parse` + a numpy-shape lint pass on `python_code` before submitting to gsim; if it fails, ask the LLM to repair without consuming a gsim run.
6. **Tooling fix** — make rescue and negate-retry first-class rows in `run_log.json`. Currently rescue's micro-edit factor is only visible in the launch log (`upgraded_v4.log`) and negate-retry overwrites the original entry's Sharpe in place. A future trainer auditing `run_log.json` alone will mis-count both. Suggested implementation: append a separate row with `rescue_of=<original_iter>`, `rescue_reason=<weak_sharpe|weak_ret|high_corr>`, the rescued factor's metrics, and an `accepted` flag; for negate-retry, preserve `original_sharpe` alongside the post-flip Sharpe.
7. **Tooling fix** — do NOT use `env -i` for real-API launches. Either run with the full inherited env (proven working in v4 relaunch) or, if isolation is required for safety, explicitly pass `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and the relevant locale variables in addition to the auth/proxy set. Add a real-API CONCURRENT smoke (multi-call, parallel) so future regressions of the v2/v4 hang shape are caught earlier.

## Trend Chart

Cross-version top-3 Sharpe trend can be regenerated via:

```
python baseline/plot_top3_trend.py --versions baseline v1 v3 v4 --metric '夏普比率' --out docs/alpha_miner_baseline_to_v4_top3_trend.png
```

(deferred; the alpha_miner runs are not under `output/baseline_<v>/` so the plot script's path glob would need either symlinks or a `--output-root` override pointing at `output/alpha_miner_runs/<run>/run_log.json`. Recommend the researcher migrate the alpha_miner output naming to the `output/baseline_<v>/<exp_id>/metrics.json` pattern in a follow-up so this script works without modification.)
