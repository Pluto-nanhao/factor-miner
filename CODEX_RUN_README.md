# 用 Codex 运行 tinyKaggleClaw

这份文档说明如何在本机安装 Codex CLI、用 ChatGPT Plus 登录，并让 Codex 进入 `tinyKaggleClaw` 项目执行检查、启动、排错和持续迭代任务。

## 1. 安装 Node.js

Codex CLI 通过 `npm` 安装。Ubuntu/Debian 环境可以使用 NodeSource 的 LTS 源：

```bash
sudo apt update
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs
```

确认版本：

```bash
node -v
npm -v
```

## 2. 安装 Codex CLI

```bash
sudo npm install -g @openai/codex@latest
codex --version
```

如果机器上已经装过 Codex，也建议定期更新：

```bash
sudo npm install -g @openai/codex@latest
```

## 3. 用 ChatGPT Plus 登录

推荐使用设备授权登录：

```bash
codex login --device-auth
```

命令会输出一个登录链接和设备码。按提示在浏览器中完成登录后，检查状态：

```bash
codex login status
```

## 4. 进入项目目录

本机当前项目路径：

```bash
cd /mnt/storage/work/hwang/tinyKaggleClaw
```

如果是在另一台机器上使用，请替换成你自己的 clone 路径：

```bash
cd /path/to/tinyKaggleClaw
```

## 5. 启动 Codex

从项目根目录启动：

```bash
codex
```

第一次进入项目时，建议先让 Codex 读取项目说明：

```text
请先阅读 README.md、FACTOR_MINING_README.md 和 AGENTS.md，理解这个项目的运行方式。不要修改代码，先总结如何启动、查看状态和停止进程。
```

## 6. 让 Codex 启动项目

项目推荐入口是 `ai_start.sh`。可以直接在 Codex 里下发：

```text
检查当前进程和项目状态，然后用 ./ai_start.sh status 查看 tinyKaggleClaw 是否已经在运行。
```

如果没有运行，再让 Codex 启动：

```text
请从项目根目录启动 tinyKaggleClaw。优先使用 ./ai_start.sh start。启动后检查状态，并告诉我 runtime board 和 training queue board 的访问地址。
```

常用人工命令如下：

```bash
./ai_start.sh guide
./ai_start.sh status
./ai_start.sh start
./ai_start.sh logs
```

启动后常用页面：

```text
Runtime board: http://127.0.0.1:8090/runtime
Training queue board: http://127.0.0.1:8100/
```

如果服务绑定到 `0.0.0.0`，也可以用服务器 IP 从另一台机器访问。

## 7. 运行因子挖掘

本项目的因子挖掘操作手册是 [FACTOR_MINING_README.md](FACTOR_MINING_README.md)。常用入口：

```bash
./ai_start.sh forever
./ai_start.sh replicate
./ai_start.sh status
./ai_start.sh logs
```

可以让 Codex 执行：

```text
请按 FACTOR_MINING_README.md 检查因子挖掘守护进程、factors.directory 复现守护进程和最近日志。只汇报状态，不要杀进程。
```

启动持续挖掘：

```text
请按 FACTOR_MINING_README.md 启动主因子挖掘和 factors.directory 复现。启动前先检查是否已有同类进程，避免重复启动。
```

## 8. 给 Codex 的常用指令模板

检查项目：

```text
检查 tinyKaggleClaw 当前状态：进程、端口、日志、最近 output。不要修改文件。
```

排查失败：

```text
查看最近一次失败的因子挖掘 run，定位失败原因，给出最小修复方案；如果需要改代码，先说明会改哪些文件。
```

继续挖掘：

```text
根据 output/factor_mining 下最近结果，继续推进因子挖掘。遵守 AGENTS.md 的并发限制，不要无限开进程。
```

复现 factors.directory：

```text
检查 output/factors_directory 和 replication ledger，继续复现未完成目标。启动前确认没有重复的 replication daemon。
```

## 9. 注意事项

- 从项目根目录运行 Codex，避免 Codex 找不到相对路径。
- 启动前先让 Codex 检查已有进程，尤其是 `local_factor_miner`、`run_factor_mining_forever.py`、`run_factors_directory_replication_forever.py`。
- 不要同时手动启动很多轮挖掘；默认并发约定见 `AGENTS.md`。
- 长任务优先通过 `./ai_start.sh` 和项目脚本启动，避免临时命令散落。
- 重要代码改动后，让 Codex 运行相关测试或最小 dry run，再继续长时间任务。

