# Quant Strategy Optimization — Start Prompt

This recipe drives a full-cycle quantitative factor mining workflow on local A-stock gsim data.

Default first action: use the repo-native local factor miner:

```bash
baseline/run_factor_mining_v1.sh
```

Do not call remote LLM APIs, WQ BRAIN, or tushare unless the human explicitly asks.

## Phase 1 — Understand the Direction

1. Read the human's direction message carefully.
2. If the human provides a reference strategy path, read the strategies there to understand:
   - Trading logic and signal generation approach
   - Stock universe and filtering criteria
   - Position sizing and risk management rules
   - Rebalancing frequency
3. Use web search or MCP tools to gather relevant domain knowledge about the direction.
4. Summarize findings in the shared thread before proceeding.

## Phase 2 — Data Preparation

1. Prefer local gsim data at `/datasvc/data/cc` through `/usr/local/gsim`.
2. Do not download tushare data in the default factor-mining path.
3. Verify local runs by checking the run-level `launch.log` and final `simsummary` metrics only.
4. Do not read large per-iter `gsim.log` files while a backtest is still running unless the process failed.

## Phase 3 — Build Backtest Infrastructure

The researcher should maintain the repo-native local factor-mining framework under `src/baseline/`:

- `src/baseline/local_factor_miner.py` — Codex-prompted local factor codegen + gsim XML writer + parallel gsim runner + simsummary parser.
- `baseline/run_factor_mining_v1.sh` — formal entry script for a 40-iteration, parallel-8 mining run.
- `output/factor_mining/` — run artifacts. Each run has a top-level `launch.log` and per-iter code/XML/pnl/gsim.log.
- `docs/` — design notes and result summaries for each mining version.

## Phase 4 — Iterative Strategy Development

Follow the standard baseline version iteration:

1. **Design**: researcher proposes factor-mining v1 directions. Write design doc to `docs/factor_mining_v1.md`.
2. **Implement**: update the factor-generation prompt, validation, or result filters in `src/baseline/local_factor_miner.py`; do not replace this with fixed template recombination.
3. **Dry run**: researcher validates code/XML generation with `python -m src.baseline.local_factor_miner --iters 2 --dry-run`.
4. **Run**: trainer starts `baseline/run_factor_mining_v1.sh`.
5. **Analyze**: after completion, trainer reads the run-level `launch.log` and writes a result summary to `docs/factor_mining_v1_result.md`.
6. **Review**: leader reviews which direction families worked and decides the next prompt or validation changes.

## Phase 5 — Version Review (Every Version)

After each completed version:

1. Compare backtest results against expectations and market knowledge.
2. Identify what worked and what did not.
3. Check for overfitting risks: does the strategy exploit a real market pattern or just noise?
4. Leader uses `/skill-creator` to update skills about strategy optimization patterns and A-stock market knowledge.
5. Document the review in `docs/` or the shared thread.
6. Turn insights into concrete next-version experiments.

## Key Principles

- **Local-first factor mining**: default to local gsim and generated AlphaBase classes.
- **CPU-only for model training**: if the strategy uses XGBoost or similar models, train on CPU.
- **Complete metrics**: every backtest result must include the full metrics set. No partial reporting.
- **Backtest period**: 2025-01-01 to present unless human specifies otherwise.
- **No overfitting**: be skeptical of strategies that work perfectly in backtest. Check robustness across different time windows and stock subsets.
- **Continuous iteration**: finishing one version is not finishing the task. Keep improving until human says stop or acceptance criteria are met.
