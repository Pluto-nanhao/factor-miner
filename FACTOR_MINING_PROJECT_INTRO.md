# Alpha Factor Mining Pipeline for gsim

一个基于 Codex/LLM 和 gsim 的自动化 A 股日内 Alpha 因子挖掘、复现、回测和优化系统。系统可以持续生成因子代码，调用本地 gsim 回测，解析绩效指标，并根据失败经验、相关性、换手和收益表现继续迭代。

## 项目简介

`tinyKaggleClaw` 的因子挖掘模块面向本地量化研究环境，目标不是一次性生成一个因子，而是搭建一条可以长期运行的自动化研发流水线。

当前流水线覆盖：

- 因子生成：由多代理讨论和 Codex codegen 生成可运行的 `AlphaBase` 因子代码
- 因子复现：从 factors.directory 可行策略库中逐个复现目标因子
- 自动回测：为每个候选生成 gsim XML 配置并调用 `/usr/local/gsim/run.py`
- 结果解析：通过 gsim simsummary 解析 Sharpe、收益、换手、回撤等指标
- 自动筛选：按收益门槛、换手门槛和相关性门槛判断是否入库
- 反向重试：对明显负向但有强信号的因子自动生成 `Neg` 版本再测
- 拯救优化：对接近门槛或高相关的候选进行 rescue、decay 等二次优化
- 长期守护：主挖掘和 factors.directory 复现都可以作为 daemon 持续运行

系统强调本地可控和可审计：所有 prompt、agent 输出、生成代码、XML、gsim 日志、PnL 和汇总结果都会保存在 `output/factor_mining/` 下，方便人工复查。

## 项目结构

```text
tinyKaggleClaw/
├── ai_start.sh                         # 面向人和 AI 的统一启动入口
├── FACTOR_MINING_README.md             # 因子挖掘操作手册
├── CODEX_RUN_README.md                 # Codex 安装和运行说明
├── AGENTS.md                           # 多代理运行约定
├── baseline/
│   └── run_factor_mining_v1.sh         # 单轮主因子挖掘入口
├── scripts/
│   ├── run_factor_mining_forever.py    # 主因子挖掘守护进程
│   ├── run_factors_directory_replication_forever.py
│   │                                   # factors.directory 复现守护进程
│   ├── rescue_specific_candidates.py   # 指定候选 rescue
│   └── rescue_run_when_complete.py     # 等 run 完成后 rescue
├── src/baseline/
│   └── local_factor_miner.py           # 核心因子生成、回测、筛选和优化逻辑
├── output/
│   ├── factor_mining/                  # 每轮因子挖掘和复现输出
│   ├── factors_directory/              # factors.directory 策略库和分类结果
│   └── factors_directory_replication/  # 复现 ledger 和状态文件
├── logs/                               # 守护进程日志
├── docs/                               # 研究说明和结果文档
└── research_notes/                     # 长期失败经验和知识沉淀
```

单个 run 的典型结构：

```text
output/factor_mining/factor_mining_IntraDay_YYYYMMDD_HHMMSS/
├── launch.log
├── results.csv
├── results.jsonl
├── run_status.md
├── rescue_candidates.csv
├── continuous_rescue.csv
├── agent_feedback_memory.md
├── iter_001/
│   ├── factor_prompt.md
│   ├── agent_discussion.md
│   ├── agent_response.md
│   ├── Alpha*.py
│   ├── Config.tinyclaw.xml
│   ├── gsim.log
│   ├── negate_retry.txt
│   ├── Config.tinyclaw.neg.xml
│   └── gsim.neg.log
└── iter_040/
```

## 快速开始

进入项目根目录：

```bash
cd /mnt/storage/work/hwang/tinyKaggleClaw
```

查看帮助和状态：

```bash
./ai_start.sh guide
./ai_start.sh status
```

启动完整运行环境：

```bash
./ai_start.sh start
```

只启动主因子挖掘：

```bash
./ai_start.sh forever
```

只启动 factors.directory 复现：

```bash
./ai_start.sh replicate
```

查看日志：

```bash
./ai_start.sh logs
```

## 运行前提

项目默认依赖本机已经准备好的 gsim 环境和 Codex CLI：

```text
Python: .venv/bin/python
Codex CLI: codex
gsim Python: /usr/local/gsim/.venv/bin/python
gsim runner: /usr/local/gsim/run.py
gsim simsummary: /usr/local/gsim/tools/simsummary.py
历史 PnL 库: /mnt/storage/work/hwang/pnl
```

常用安装和登录 Codex 的步骤见 [CODEX_RUN_README.md](CODEX_RUN_README.md)。

## 工作流程

### 1. 多代理讨论

每个候选因子先经过 researcher、reviewer、synthesizer 三个角色讨论，输出 `agent_discussion.md`。讨论会约束数据字段、经济假设、实现风险、未来函数风险、换手风险和与已安装因子的相似性。

### 2. 因子代码生成

Codex 根据 `factor_prompt.md` 生成一个完整 Python 文件。生成代码必须满足：

- 定义一个 `AlphaBase` 子类
- 只使用本地 gsim 数据
- 支持 delay=0
- 使用向量化 numpy
- 避免未来数据
- 生成横截面归一化后的 `self.alpha[valid_idx]`

### 3. gsim 回测

系统自动写出 `Config.tinyclaw.xml`，调用 gsim 跑完整区间：

```text
20190101 - 20241231
```

回测结果通过 simsummary 解析后写入：

```text
results.csv
results.jsonl
run_status.md
```

### 4. 筛选和入库

默认门槛包括：

```text
Sharpe >= 3.0
ret_pct >= 20.0
tvr_pct < 60.0
max_corr < 0.7
```

通过门槛的因子会进入安装流程；未通过的因子会记录失败原因，供后续 prompt 和反馈池使用。

### 5. 反向、decay 和 rescue

系统会对部分失败因子自动尝试二次处理：

- `Neg`：如果原始因子 Sharpe 和收益显著为负，自动取反再测
- `decay`：如果收益足够但换手过高，尝试更长 decay
- `near-miss rescue`：如果 Sharpe/收益接近门槛，生成 moderate rescue 版本
- `high-corr rescue`：如果表现够好但相关性偏高，尝试结构性降相关

这些产物会保存在对应 `iter_xxx/` 目录中。

## factors.directory 复现

复现守护进程会读取 `output/factors_directory/` 中已筛出的 5m 可行策略，并逐个目标运行 40 个候选。

启动：

```bash
./ai_start.sh replicate
```

复现状态写入：

```text
output/factors_directory_replication/state.json
output/factors_directory_replication/replication_ledger.csv
```

复现 run 目录形如：

```text
output/factor_mining/factor_mining_FactorsDir_0001_momentum-based-on-rankin_YYYYMMDD_HHMMSS/
```

复现模式是严格目标模式：prompt 中的 factors.directory 目标覆盖通用探索方向，日志会显示：

```text
strict_replication=true
[iter 1] replication target: <slug>
```

## 输出指标

每个因子至少记录：

- `sharpe`：夏普比率
- `ret_pct`：收益率
- `tvr_pct`：换手率
- `dd_pct`：最大回撤
- `fitness`：gsim 汇总指标
- `accepted`：是否通过筛选
- `reason`：拒绝或接受原因
- `max_corr`：与现有 PnL 库最大相关性

示例：

```csv
iteration,factor,sharpe,ret_pct,tvr_pct,dd_pct,fitness,accepted,reason,max_corr
15,AlphaFD0028D6I15Neg,2.28,19.15,35.64,15.24,1.67,False,return_gate_failed,
```

## 核心模块说明

### `src/baseline/local_factor_miner.py`

核心 pipeline，负责：

- 构造 prompt
- 调用 Codex
- 校验生成代码
- 写出 gsim XML
- 调用 gsim 回测
- 解析 simsummary
- 执行 Neg、decay、rescue
- 记录结果和失败知识

### `scripts/run_factor_mining_forever.py`

主因子挖掘守护进程，负责持续启动 `IntraDay` 挖掘轮次，并在一轮完成后异步处理 rescue。

### `scripts/run_factors_directory_replication_forever.py`

factors.directory 复现守护进程，负责按 ledger/state 逐个目标复现，并将每个目标的结果写入 `replication_ledger.csv`。

### `ai_start.sh`

统一操作入口，封装状态检查、启动、日志查看、主挖掘和复现守护进程。

## 常用操作

查看当前运行状态：

```bash
./ai_start.sh status
```

查看复现日志：

```bash
tail -f logs/factors_directory_replication.log
```

查看最近一轮结果：

```bash
ls -td output/factor_mining/factor_mining_* | head
```

查看某轮汇总：

```bash
cat output/factor_mining/<run_dir>/run_status.md
```

查看某个 iter 的生成逻辑：

```bash
cat output/factor_mining/<run_dir>/iter_001/factor_prompt.md
cat output/factor_mining/<run_dir>/iter_001/agent_discussion.md
cat output/factor_mining/<run_dir>/iter_001/Alpha*.py
```

## 注意事项

- 启动前先检查已有进程，避免重复启动多个 daemon。
- factors.directory 复现和主挖掘分开运行，默认都落到 `output/factor_mining/`。
- 删除复现结果前必须先停止 `run_factors_directory_replication_forever.py` 及其子进程。
- 不要把 API key 或本地敏感路径提交到公开仓库。
- 长时间运行时优先看 `run_status.md`、`results.csv` 和守护进程日志，不要只看单个 `gsim.log`。
- 生成因子是否值得保留，以完整区间回测、换手、相关性和稳定性为准。
