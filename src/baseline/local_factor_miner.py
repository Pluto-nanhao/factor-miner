"""Local gsim factor mining runner for tinyKaggleClaw.

This is a repo-native factor-mining entry point. It does not call Claude,
DeepSeek, WQ BRAIN, tushare, or any remote market-data service. It generates
local AlphaBase candidates, runs gsim, and records only run-level status plus
final simsummary metrics.
"""

from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from concurrent.futures import Future
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = Path(os.environ.get("FACTOR_MINER_WORK_DIR", "/mnt/storage/work/hwang"))
RUN_ROOT = Path(os.environ.get("FACTOR_MINER_RUN_ROOT", str(REPO_ROOT / "output" / "factor_mining")))
GSIM_PYTHON = Path(os.environ.get("GSIM_PYTHON", "/usr/local/gsim/.venv/bin/python"))
GSIM_RUN = Path(os.environ.get("GSIM_RUN", "/usr/local/gsim/run.py"))
GSIM_SUMMARY = Path(os.environ.get("GSIM_SUMMARY", "/usr/local/gsim/tools/simsummary.py"))
BCORR_BIN = Path(os.environ.get("FACTOR_MINER_BCORR", "/usr/local/gsim/dataops/bcorr"))
CODEX_BIN = os.environ.get("FACTOR_MINER_CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("FACTOR_MINER_CODEX_MODEL", "gpt-5.4")
CODEGEN_TEMPERATURE = os.environ.get("FACTOR_MINER_CODEGEN_TEMPERATURE", "1.0").strip()
DISCUSSION_MODEL = os.environ.get("FACTOR_MINER_DISCUSSION_MODEL", "gpt-5.4")
FEEDBACK_MODEL = os.environ.get("FACTOR_MINER_FEEDBACK_MODEL", DISCUSSION_MODEL)
AGENT_TIMEOUT = int(os.environ.get("FACTOR_MINER_AGENT_TIMEOUT", "300"))
AGENT_RETRIES = int(os.environ.get("FACTOR_MINER_AGENT_RETRIES", "4"))
AGENT_RETRY_BASE_SLEEP = int(os.environ.get("FACTOR_MINER_AGENT_RETRY_BASE_SLEEP", "20"))
DECAY_DAYS = int(os.environ.get("FACTOR_MINER_DECAY_DAYS", "5"))
DISCUSSION_AGENTS = int(os.environ.get("FACTOR_MINER_DISCUSSION_AGENTS", "3"))
DISCUSSION_PARALLEL = os.environ.get("FACTOR_MINER_DISCUSSION_PARALLEL", "true").lower() not in {"0", "false", "no"}
ASYNC_FEEDBACK = os.environ.get("FACTOR_MINER_ASYNC_FEEDBACK", "true").lower() not in {"0", "false", "no"}
FEEDBACK_TAIL_CHARS = int(os.environ.get("FACTOR_MINER_FEEDBACK_TAIL_CHARS", "12000"))
CODEGEN_PARALLEL = int(os.environ.get("FACTOR_MINER_CODEGEN_PARALLEL", "6"))
PRESCREEN_ENABLED = os.environ.get("FACTOR_MINER_PRESCREEN", "false").lower() in {"1", "true", "yes"}
PRESCREEN_START_DATE = os.environ.get("FACTOR_MINER_PRESCREEN_START_DATE", "20220101")
PRESCREEN_MIN_SHARPE = float(os.environ.get("FACTOR_MINER_PRESCREEN_MIN_SHARPE", "2.5"))
PRESCREEN_MIN_RET = float(os.environ.get("FACTOR_MINER_PRESCREEN_MIN_RET", "15.0"))
PRESCREEN_TIMEOUT = int(os.environ.get("FACTOR_MINER_PRESCREEN_TIMEOUT", "420"))
PNL_DIR = Path(os.environ.get("FACTOR_MINER_PNL_DIR", str(WORK_DIR / "pnl")))
DROPBOX_SUBMIT_ROOT = Path(os.environ.get("FACTOR_MINER_DROPBOX_SUBMIT_ROOT", "/mnt/storage/dropbox/hwang"))
INSTALLED_FACTOR_LEDGER_CSV = Path(
    os.environ.get("FACTOR_MINER_INSTALLED_LEDGER_CSV", str(WORK_DIR / "INSTALLED_FACTOR_LEDGER.csv"))
)
INSTALLED_FACTOR_LEDGER_JSON = Path(
    os.environ.get("FACTOR_MINER_INSTALLED_LEDGER_JSON", str(WORK_DIR / "INSTALLED_FACTOR_LEDGER.json"))
)
INSTALLED_FACTOR_LEDGER_MD = Path(
    os.environ.get("FACTOR_MINER_INSTALLED_LEDGER_MD", str(WORK_DIR / "INSTALLED_FACTOR_LEDGER.md"))
)
MIN_SHARPE = float(os.environ.get("FACTOR_MINER_MIN_SHARPE", "3.0"))
MIN_RET = float(os.environ.get("FACTOR_MINER_MIN_RET", "20.0"))
MAX_CORR = float(os.environ.get("FACTOR_MINER_MAX_CORR", "0.7"))
MAX_TVR = float(os.environ.get("FACTOR_MINER_MAX_TVR", "60.0"))
HIGH_CORR_RESCUE_MAX = float(os.environ.get("FACTOR_MINER_HIGH_CORR_RESCUE_MAX", "0.85"))
HIGH_CORR_WIDE_RESCUE_MAX = float(os.environ.get("FACTOR_MINER_HIGH_CORR_WIDE_RESCUE_MAX", "0.97"))
HIGH_CORR_SKIP_RESCUE_MAX = float(os.environ.get("FACTOR_MINER_HIGH_CORR_SKIP_RESCUE_MAX", "0.85"))
DECAY_RETRY_DAYS = [
    int(x) for x in os.environ.get("FACTOR_MINER_DECAY_RETRY_DAYS", "8,10,15,20").split(",") if x.strip()
]
DOUBLE_DECAY_RETRY_PAIRS = [
    tuple(int(part) for part in item.split("+", 1))
    for item in os.environ.get("FACTOR_MINER_DOUBLE_DECAY_RETRY_PAIRS", "5+8,8+10,10+15").split(",")
    if item.strip() and "+" in item
]
NEGATE_RETRY_MIN_ABS_SHARPE = float(os.environ.get("FACTOR_MINER_NEGATE_RETRY_MIN_ABS_SHARPE", "2.0"))
NEGATE_RETRY_MIN_ABS_RET = float(os.environ.get("FACTOR_MINER_NEGATE_RETRY_MIN_ABS_RET", "10.0"))
RESCUE_SHARPE_MARGIN = float(os.environ.get("FACTOR_MINER_RESCUE_SHARPE_MARGIN", "0.5"))
RESCUE_RET_MARGIN = float(os.environ.get("FACTOR_MINER_RESCUE_RET_MARGIN", "5.0"))
POWER_RESCUE_EXPS = tuple(
    float(item)
    for item in os.environ.get("FACTOR_MINER_POWER_RESCUE_EXPS", "1.25,1.5,1.75,2.0").split(",")
    if item.strip()
)
RESEARCH_NOTES_DIR = Path(os.environ.get("FACTOR_MINER_RESEARCH_NOTES", str(WORK_DIR / "research_notes")))
KNOWLEDGE_TAIL_CHARS = int(os.environ.get("FACTOR_MINER_KNOWLEDGE_TAIL_CHARS", "16000"))
RESEARCH_TOPIC = os.environ.get("FACTOR_MINER_RESEARCH_TOPIC", "intraday_factor_mining")
DUPLICATE_CHECK_ENABLED = os.environ.get("FACTOR_MINER_DUPLICATE_CHECK", "true").lower() not in {"0", "false", "no"}
FACTOR_LIBRARY_REPLICATION_BIAS = os.environ.get("FACTOR_MINER_LIBRARY_REPLICATION_BIAS", "true").lower() not in {"0", "false", "no"}
FACTOR_LIBRARY_TARGET_SLUG = os.environ.get("FACTOR_MINER_LIBRARY_TARGET_SLUG", "").strip()
FACTOR_LIBRARY_TARGET_TITLE = os.environ.get("FACTOR_MINER_LIBRARY_TARGET_TITLE", "").strip()
FACTOR_LIBRARY_TARGET_URL = os.environ.get("FACTOR_MINER_LIBRARY_TARGET_URL", "").strip()
FACTOR_LIBRARY_TARGET_TEXT = os.environ.get("FACTOR_MINER_LIBRARY_TARGET_TEXT", "").strip()
ONE_FAMILY_PER_RUN = os.environ.get("FACTOR_MINER_ONE_FAMILY_PER_RUN", "false").lower() not in {"0", "false", "no"}
FORECAST_DATA_ENABLED = os.environ.get("FACTOR_MINER_ENABLE_FORECAST_DATA", "true").lower() not in {
    "0",
    "false",
    "no",
}
CC_ALL_EXTRA_DATA_ENABLED = os.environ.get("FACTOR_MINER_ENABLE_CC_ALL_EXTRA_DATA", "true").lower() not in {
    "0",
    "false",
    "no",
}
CC_ALL_DATA_ROOT = Path(os.environ.get("FACTOR_MINER_CC_ALL_DATA_ROOT", "/cache/data/cc_all"))
NIO_DATA_PATH = os.environ.get(
    "FACTOR_MINER_NIODATAPATH",
    "/cache/data/cc_all" if (FORECAST_DATA_ENABLED or CC_ALL_EXTRA_DATA_ENABLED) else "/datasvc/data/cc",
)
FORECAST_TABLE_FIELDS = {
    "revenue_forecast_annual": ("revenue", "forecast_year"),
    "revenue_forecast_quarter": ("revenue", "forecast_quarter"),
    "income_statement_fore_annual": (
        "IS01", "IS02", "IS03", "IS04", "IS05", "IS06", "IS07", "IS08", "IS09",
        "IS11", "IS12", "IS13", "IS14", "IS15", "IS16", "IS17", "IS18", "IS19",
        "IS20", "IS21", "IS22", "IS23", "IS24", "IS25", "IS26", "IS27", "IS28",
        "IS29", "IS30", "IS31", "IS32", "IS33", "IS34", "IS35", "IS36", "IS37",
        "IS38", "IS39", "IS40", "IS41", "IS42", "forecast_year",
    ),
    "income_statement_fore_quarter": (
        "IS01_q", "IS02_q", "IS03_q", "IS04_q", "IS05_q", "IS06_q", "IS07_q",
        "IS08_q", "IS09_q", "IS11_q", "IS12_q", "IS13_q", "IS14_q", "IS15_q",
        "IS16_q", "IS17_q", "IS18_q", "IS19_q", "IS30_q", "IS31_q", "IS32_q",
        "IS33_q", "IS34_q", "IS35_q", "IS36_q", "IS37_q", "IS38_q", "IS39_q",
        "IS40_q", "IS41_q", "IS42_q", "forecast_quarter",
    ),
    "balance_sheet_fore_annual": (
        "BS01", "BS02", "BS03", "BS04", "BS05", "BS06", "BS07", "BS08", "BS09", "BS10",
        "BS11", "BS12", "BS13", "BS14", "BS15", "BS16", "BS17", "BS18", "BS19", "BS20",
        "BS21", "BS22", "BS23", "BS24", "BS25", "BS26", "BS27", "BS28", "BS29", "BS30",
        "BS31", "BS32", "BS33", "BS34", "BS35", "BS36", "BS37", "BS38", "BS39", "BS40",
        "forecast_year",
    ),
    "cash_flow_statement_fore_annual": (
        "CF01", "CF02", "CF03", "CF04", "CF05", "CF06", "CF07", "CF08", "CF09", "CF10",
        "CF11", "CF12", "CF13", "CF14", "CF15", "CF16", "CF17", "CF18", "CF19", "CF20",
        "CF21", "CF22", "forecast_year",
    ),
    "finance_ratio_fore_annual": (
        "ID01", "ID02", "ID03", "ID04", "ID05", "ID06", "ID08", "ID09", "ID10", "ID11",
        "ID12", "ID13", "ID14", "ID15", "ID17", "ID18", "ID19", "ID20", "ID21", "ID23",
        "ID24", "ID25", "ID26", "ID27", "ID29", "ID30", "ID31", "ID32", "ID34", "ID35",
        "ID36", "ID37", "ID38", "ID40", "ID41", "ID42", "forecast_year",
    ),
    "financial_summary_fore_annual": (
        "IS01", "IS02", "IS23", "ID05", "IS20", "ID04", "IS36", "ID06", "IS42",
        "ID31", "ID32", "IS06", "IS24", "IS21", "IS35", "ID13", "ID15",
        "ID35", "ID36", "ID38", "ID42", "forecast_year",
    ),
    "financial_summary_fore_quarter": (
        "IS01_q", "IS02_q", "IS36_q", "ID06_q", "IS42_q", "IS06_q",
        "IS35_q", "ID13_q", "ID15_q", "ID35_q", "ID36_q", "forecast_quarter",
    ),
}

FORECAST_DATA_MODULES = "\n".join(
    (
        f'    <Data id="{table}" module="/usr/local/gsim/source_ref/DmgrWbai_AIFcst_{table}.py" '
        f'dataPath="/cache/data/cc_all/{table}" niomapprivate="true"/>'
    )
    for table in FORECAST_TABLE_FIELDS
) + "\n"

CORE_XML_TABLES = {
    "ALL",
    "ALL_TRD",
    "Basedata",
    "PriceLimit",
    "adjfactor",
    "ipo",
    "ashareeodprices",
    "aindexeodprices",
    "Interval5m",
}
UNIVERSE_ONLY_TABLES = {
    "ALL",
    "ALL_GIM",
    "ALL_TRD",
    "FULL",
    "HS300",
    "TOP1000",
    "TOP1500",
    "TOP2000",
    "TOP2600",
    "TOP3000",
    "TOP3300",
    "TOP4000",
    "ZZ500",
    "ZZ1000",
    "__universe",
}


def _discover_cc_all_fields(root: Path = CC_ALL_DATA_ROOT) -> dict[str, tuple[str, ...]]:
    if not root.exists():
        return {}
    fields_by_table: dict[str, tuple[str, ...]] = {}
    for table_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        table = table_dir.name
        fields = []
        for data_file in sorted(table_dir.glob("*.npy")):
            name = data_file.name[:-4]
            prefix = f"{table}."
            fields.append(name[len(prefix):] if name.startswith(prefix) else name)
        if fields:
            fields_by_table[table] = tuple(fields)
    return fields_by_table


def _cc_all_dataset_name(table: str, field: str, root: Path = CC_ALL_DATA_ROOT) -> str:
    table_dir = root / table
    prefixed_name = f"{table}.{field}"
    if (table_dir / f"{prefixed_name}.npy").exists():
        return prefixed_name
    if (table_dir / f"{field}.npy").exists():
        return field
    return prefixed_name


def _is_self_named_singleton_table(table: str, fields: tuple[str, ...], root: Path = CC_ALL_DATA_ROOT) -> bool:
    return fields == (table,) and (root / table / f"{table}.npy").exists()


def _find_cc_all_module_path(table: str) -> Path | None:
    search_dirs = [
        Path("/usr/local/gsim/source_ref"),
        Path("/usr/local/gsim/src_loader"),
        Path("/usr/local/gsim/dm_src"),
        Path("/usr/local/gsim/gsim/data/module"),
        Path("/usr/local/gsim/gsim/data/module/module"),
    ]
    candidate_names = []
    if table in FORECAST_TABLE_FIELDS:
        candidate_names.append(f"DmgrWbai_AIFcst_{table}.py")
    candidate_names.extend([f"Dmgr_{table}.py", f"Dmgr{table}.py"])
    if table.startswith("Dmgr"):
        candidate_names.append(f"{table}.py")
    for directory in search_dirs:
        for name in candidate_names:
            path = directory / name
            if path.exists():
                return path
    return None


CC_ALL_TABLE_FIELDS = _discover_cc_all_fields()
AUTO_CC_ALL_MODULE = REPO_ROOT / "src" / "baseline" / "DmgrAutoCCAll.py"
CC_ALL_MODULE_PATHS = {
    table: module_path
    for table, fields in CC_ALL_TABLE_FIELDS.items()
    if table not in CORE_XML_TABLES
    and table not in FORECAST_TABLE_FIELDS
    and table not in UNIVERSE_ONLY_TABLES
    and not _is_self_named_singleton_table(table, fields)
    for module_path in [_find_cc_all_module_path(table) or AUTO_CC_ALL_MODULE]
}

CC_ALL_EXTRA_DATA_MODULES = "\n".join(
    (
        f'    <Data id="{table}" module="{module_path}" '
        f'dataPath="{CC_ALL_DATA_ROOT / table}" niomapprivate="true"/>'
    )
    for table, module_path in sorted(CC_ALL_MODULE_PATHS.items())
) + ("\n" if CC_ALL_MODULE_PATHS else "")

CC_ALL_REGISTERED_TABLES = CORE_XML_TABLES | set(FORECAST_TABLE_FIELDS) | set(CC_ALL_MODULE_PATHS)
CC_ALL_REGISTERED_DATASETS = {
    _cc_all_dataset_name(table, field)
    for table in CC_ALL_REGISTERED_TABLES
    for field in CC_ALL_TABLE_FIELDS.get(table, ())
}


DIRECTION_VECTOR_AXES = [
    "daily_price",
    "daily_volume_amount",
    "intraday_price_path",
    "intraday_amount_path",
    "forecast_revenue",
    "forecast_profitability",
    "forecast_revision",
    "cross_sectional_state",
    "own_history_baseline",
    "event_time_segmentation",
    "nonlinear_distribution",
    "return_momentum",
    "return_reversal",
    "liquidity_regime",
    "volatility_regime",
    "price_volume_divergence",
    "time_of_day_seasonality",
    "tail_extreme_focus",
    "persistence_decay_control",
]
FORECAST_DIRECTION_AXES = {"forecast_revenue", "forecast_profitability", "forecast_revision"}

DIRECTION_CANDIDATES = [
    {
        "title": "开盘冲击的成交额吸收簇",
        "cluster": "open_liquidity_absorption",
        "vector": [0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1, 0],
        "data": "Interval5m.open/close/amo + daily amount",
        "mechanism": "compare opening return shock with same-bar amount absorption and market-breadth state",
        "avoid": "plain opening return reversal without amount or breadth conditioning",
    },
    {
        "title": "尾盘价格修复与成交后置簇",
        "cluster": "close_repair_backload",
        "vector": [1, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1],
        "data": "late-day Interval5m.close/open/amo + own 5m amount baseline",
        "mechanism": "separate late repair path from whether liquidity arrives in the final event-time bars",
        "avoid": "fixed last-minus-first momentum",
    },
    {
        "title": "日内价格位置和成交位置错配簇",
        "cluster": "price_amount_location_mismatch",
        "vector": [1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0],
        "data": "Interval5m.close/open/amo + daily high/low/close",
        "mechanism": "measure whether trading concentrates at price locations inconsistent with final daily location",
        "avoid": "generic VWAP distance or amount-weighted close alone",
    },
    {
        "title": "成交额峰值事件后的回撤/延续簇",
        "cluster": "amount_peak_followthrough",
        "vector": [0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0],
        "data": "Interval5m.amo peak bars + adjacent Interval5m returns",
        "mechanism": "use amount-peak event time and compare post-peak price path with pre-peak pressure",
        "avoid": "full-day amount autocorrelation",
    },
    {
        "title": "市场宽度条件下的过度反应簇",
        "cluster": "breadth_conditioned_overreaction",
        "vector": [0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0],
        "data": "same-bar cross-sectional Interval5m returns",
        "mechanism": "condition stock intraday return extremes on same-bar breadth and cross-sectional skew",
        "avoid": "single-stock fixed-window reversal",
    },
    {
        "title": "个股历史异常成交形态簇",
        "cluster": "own_history_abnormal_amount_shape",
        "vector": [0, 1, 0, 1, 0, 0, 0, 0, 1, 1, 1, 0, 0, 1, 0, 0, 1, 1, 1],
        "data": "Interval5m.amo shape against 3-10 day own history",
        "mechanism": "detect abnormal W-shape, midday drought, or closing burst relative to own history",
        "avoid": "raw amount level or turnover-like ratio",
    },
    {
        "title": "波动压缩后的流动性释放簇",
        "cluster": "volatility_compression_liquidity_release",
        "vector": [1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1, 0, 0, 0, 1],
        "data": "Interval5m return range proxy + Interval5m.amo + daily range",
        "mechanism": "contrast quiet price path with later abnormal liquidity release under market state",
        "avoid": "one-window volatility factor",
    },
    {
        "title": "跨日相对位置与当日微结构确认簇",
        "cluster": "daily_location_intraday_confirmation",
        "vector": [1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "daily OHLCV/amount + current-day Interval5m path",
        "mechanism": "use daily location as regime and require intraday path or amount confirmation",
        "avoid": "daily close-to-high rank alone",
    },
    {
        "title": "价格路径熵和成交集中度簇",
        "cluster": "path_entropy_amount_concentration",
        "vector": [0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0],
        "data": "Interval5m return signs/ranks + Interval5m.amo shares",
        "mechanism": "combine nonlinear path entropy with whether amount is concentrated or diffuse",
        "avoid": "plain skew/kurtosis",
    },
    {
        "title": "分时强弱切换和流动性确认簇",
        "cluster": "regime_switch_liquidity_confirm",
        "vector": [0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 0],
        "data": "morning/afternoon/event-time Interval5m returns and amo",
        "mechanism": "identify sign/regime switches that are confirmed or contradicted by amount allocation",
        "avoid": "simple morning-vs-afternoon return spread",
    },
    {
        "title": "同向市场中的个股迟滞簇",
        "cluster": "breadth_lagged_stock_response",
        "vector": [0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "same-bar breadth + stock 5m returns + stock amount-share baseline",
        "mechanism": "find stocks lagging a one-sided market move while liquidity is abnormal",
        "avoid": "market beta or broad momentum proxy",
    },
    {
        "title": "高低价区成交迁移簇",
        "cluster": "high_low_zone_amount_migration",
        "vector": [1, 0, 1, 1, 0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 0, 1, 0, 1, 0],
        "data": "daily high/low + intraday close location + Interval5m.amo",
        "mechanism": "track event-time migration of amount between high-price and low-price zones",
        "avoid": "static amount-weighted price location",
    },
    {
        "title": "AI收入预测修正与日频确认簇",
        "cluster": "forecast_revenue_revision_daily_confirm",
        "vector": [0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1],
        "data": "revenue_forecast_annual/quarter + financial_summary_fore_* + daily OHLCV/amount; no Interval5m",
        "mechanism": "treat AI revenue forecast revision or slope as the core signal and use one simple daily liquidity/price confirmation",
        "avoid": "mixing forecast fields with 5m bars or adding multiple confirmation masks",
    },
    {
        "title": "AI盈利质量预期与价格反应错配簇",
        "cluster": "forecast_profitability_daily_response_mismatch",
        "vector": [1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1],
        "data": "financial_summary_fore_annual/quarter profitability fields + daily OHLCV/amount; no Interval5m",
        "mechanism": "make forecast profitability level/change the core primitive and use one daily price/liquidity response check",
        "avoid": "plain daily reversal, pure forecast rank, or any 5m-bar confirmation",
    },
    {
        "title": "AI完整利润表预期修正簇",
        "cluster": "forecast_income_statement_revision",
        "vector": [0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, 0, 1, 1],
        "data": "income_statement_fore_annual/quarter selected IS fields + optional daily amount/volume; no Interval5m",
        "mechanism": "use one income-statement forecast field level, revision, or quarterly near-vs-far contrast as the core signal, then check whether daily liquidity has underreacted",
        "avoid": "mixing many IS fields or duplicating financial_summary_fore_quarter.IS42_q rank",
    },
    {
        "title": "AI资产负债表稳健性预期簇",
        "cluster": "forecast_balance_sheet_resilience",
        "vector": [1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 1],
        "data": "balance_sheet_fore_annual BS fields + finance_ratio_fore_annual ID fields + daily OHLCV/amount; no Interval5m",
        "mechanism": "treat one balance-sheet forecast or forecast ratio as a resilience/fragility expectation and compare it with recent daily price response",
        "avoid": "using balance-sheet fields only as a generic size proxy or blending many BS/ID fields",
    },
    {
        "title": "AI现金流质量预期与价格错配簇",
        "cluster": "forecast_cash_flow_quality_mispricing",
        "vector": [1, 1, 0, 0, 0, 1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 0, 1],
        "data": "cash_flow_statement_fore_annual CF fields + finance_ratio_fore_annual ID fields + daily OHLCV/amount; no Interval5m",
        "mechanism": "use one cash-flow forecast quality primitive and test whether daily price/liquidity has overreacted or underreacted",
        "avoid": "simple revenue/profit rank, or combining cash-flow, balance-sheet, and summary fields in one score",
    },
    {
        "title": "资金流大单冲击与日频反应簇",
        "cluster": "money_flow_large_order_daily_response",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1],
        "data": "money-flow table if registered, otherwise hf_daily/equ_factor_volume + daily OHLCV/amount; no Interval5m unless direction explicitly requires",
        "mechanism": "measure whether large-order or flow-pressure style fields are confirmed or contradicted by same-day price and liquidity state",
        "avoid": "raw amount level, simple turnover, or combining many money-flow buckets",
    },
    {
        "title": "分析师共识FY斜率与定价偏差簇",
        "cluster": "analyst_consensus_fy_slope_mispricing",
        "vector": [1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "ashareconsensusrollingdata_FY0/FY1/FY2/FY3 + optional daily OHLCV/amount; no Interval5m",
        "mechanism": "use one consensus FY slope, valuation expectation, or revision proxy and compare it with daily price/liquidity response",
        "avoid": "duplicating AI forecast revenue rank or blending multiple FY horizons with fitted weights",
    },
    {
        "title": "集合竞价预测与全天修复簇",
        "cluster": "auction_opening_prediction_repair",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1],
        "data": "hf_daily_auction_table + daily OHLCV/amount; optionally one Interval5m open/close confirmation if explicitly needed",
        "mechanism": "compare auction/opening prediction or jump state with same-day realized repair or liquidity absorption",
        "avoid": "plain overnight return reversal without auction volume or leave-ratio state",
    },
    {
        "title": "预计算风险收益因子状态切换簇",
        "cluster": "precomputed_return_risk_regime_switch",
        "vector": [1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1],
        "data": "equ_factor_return/equ_factor_trend/equ_factor_obos + daily OHLCV/amount; no Interval5m",
        "mechanism": "use one precomputed risk, beta, volatility, or trend state as a regime and test continuation versus reversal",
        "avoid": "directly outputting a single style factor rank without a distinct price-response mismatch",
    },
    {
        "title": "财务质量实际值与预测分歧簇",
        "cluster": "actual_financial_quality_forecast_disagreement",
        "vector": [1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 0, 1],
        "data": "ashareincome/asharebalancesheet/asharecashflow or equ_factor_derive/pq + one forecast or consensus field + daily OHLCV; no Interval5m",
        "mechanism": "compare actual financial quality or TTM derived quality with one expectation field, then measure daily mispricing",
        "avoid": "large multi-statement composite scores or slow pure-value factors",
    },
    {
        "title": "高频日派生流动性拥挤簇",
        "cluster": "hf_daily_liquidity_crowding",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],
        "data": "hf_daily_der_table_* + daily OHLCV/amount; no raw 5m tensors",
        "mechanism": "use one high-frequency daily derivative field as a crowding or liquidity-state proxy and compare it with daily price location",
        "avoid": "recreating existing 5m skew/kurtosis/amount autocorrelation from raw bars",
    },
    {
        "title": "开收盘资金流迁移簇",
        "cluster": "open_close_moneyflow_migration",
        "vector": [1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1],
        "data": "AShareMoneyFlow open/close moneyflow or net-inflow fields + daily OHLCV/amount; no raw 5m",
        "mechanism": "measure whether flow pressure migrates from open to close and whether daily return overreacts to the migration",
        "avoid": "simple close-open return or total flow level",
    },
    {
        "title": "集合竞价撤单压力簇",
        "cluster": "auction_leave_ratio_pressure",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0],
        "data": "hf_daily_auction_table.STAGE_TWO_LEAVE_RATIO/COMMISSION/RET + daily OHLCV/amount; no Interval5m",
        "mechanism": "use auction leave ratio or commission as unstable opening-pressure state and test later reversal versus continuation",
        "avoid": "plain JUMP_RET reversal without auction participation state",
    },
    {
        "title": "竞价预测误差与收盘修正簇",
        "cluster": "auction_prediction_error_close_repair",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1],
        "data": "hf_daily_auction_table.RET_PRED/RET_PRED_0935/JUMP_RET + daily close/open/high/low; no raw 5m",
        "mechanism": "estimate whether auction-predicted direction was corrected by the full-day price path",
        "avoid": "using RET_PRED directly as alpha without realized repair comparison",
    },
    {
        "title": "分析师FY1-FY3增速期限结构簇",
        "cluster": "consensus_fy_term_structure_growth",
        "vector": [1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "ashareconsensusrollingdata_FY1/FY2/FY3 est_oper_revenue, net_profit, est_eps + daily price/amount; no Interval5m",
        "mechanism": "use the slope or curvature of consensus FY expectations as growth term structure and compare it with daily pricing",
        "avoid": "single FY1 estimate rank or fitted weighted sum of all horizons",
    },
    {
        "title": "共识估值压缩与盈利上修簇",
        "cluster": "consensus_valuation_compression_revision",
        "vector": [1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "ashareconsensusrollingdata_FY* est_pe/est_pb/est_peg with est_eps/net_profit + daily OHLCV/amount; no Interval5m",
        "mechanism": "find stocks with improving earnings expectation but compressed consensus valuation, then test daily underpricing",
        "avoid": "pure low PE/PB value factor",
    },
    {
        "title": "FY0实际与FY1预期断层簇",
        "cluster": "fy0_actual_fy1_expectation_gap",
        "vector": [1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1],
        "data": "ashareconsensusrollingdata_FY0/FY1 net_profit, est_eps, est_roe + daily OHLCV; no Interval5m",
        "mechanism": "compare FY0 actual/proxy level with FY1 expectation gap and measure whether price has overreacted to that gap",
        "avoid": "standalone FY1 expected growth without FY0 anchor",
    },
    {
        "title": "现金流TTM与经营利润预期分歧簇",
        "cluster": "cashflow_ttm_profit_expectation_divergence",
        "vector": [1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1],
        "data": "equ_factor_derive.NetOperateCFTTM/FCFF/FCFE or asharecashflow + forecast/consensus profit field + daily OHLCV; no Interval5m",
        "mechanism": "compare cash-flow quality with profit expectation and test whether the market discounts the divergence",
        "avoid": "large multi-cashflow composite or pure cash-flow yield",
    },
    {
        "title": "资产负债表杠杆脆弱性与价格强势簇",
        "cluster": "balance_sheet_leverage_fragility_price_strength",
        "vector": [1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1],
        "data": "asharebalancesheet liabilities/debt/current assets fields or equ_factor_derive.NetDebt + daily price/amount; no Interval5m",
        "mechanism": "treat one leverage or liquidity-fragility field as risk state and test whether recent price strength is fragile",
        "avoid": "static size/leverage rank without price-response condition",
    },
    {
        "title": "盈利质量SUE与日频量价确认簇",
        "cluster": "profit_quality_sue_daily_confirmation",
        "vector": [1, 1, 0, 0, 0, 1, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "equ_factor_pq.SUE/SUOI/ROE/ROA + daily OHLCV/amount; optional consensus field but no Interval5m",
        "mechanism": "use one profitability surprise/quality field and require a simple daily liquidity confirmation or contradiction",
        "avoid": "direct SUE rank as final alpha",
    },
    {
        "title": "预计算动量与资金流背离簇",
        "cluster": "precomputed_momentum_moneyflow_divergence",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1],
        "data": "equ_factor_trend or equ_factor_return momentum/beta fields + AShareMoneyFlow or equ_factor_volume; no raw 5m",
        "mechanism": "compare precomputed trend state with one money-flow/liquidity proxy and capture divergence-driven reversal or continuation",
        "avoid": "trend rank alone or amount rank alone",
    },
    {
        "title": "波动风险因子与尾部回撤状态簇",
        "cluster": "volatility_risk_tail_drawdown_state",
        "vector": [1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1],
        "data": "equ_factor_return.RealizedVolatility/DASTD/CmraCNE5/GainLossVarianceRatio + daily OHLC; no Interval5m",
        "mechanism": "use one volatility or drawdown risk factor as a regime and test whether daily tail move is exhausted",
        "avoid": "plain realized volatility short signal",
    },
    {
        "title": "OBOS过热与资金承接簇",
        "cluster": "obos_overheat_liquidity_absorption",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 0, 1, 1],
        "data": "equ_factor_obos + equ_factor_volume or AShareMoneyFlow + daily OHLCV/amount; no raw 5m",
        "mechanism": "condition overbought/oversold state on whether liquidity absorption supports or rejects the move",
        "avoid": "single overbought/oversold reversal without liquidity state",
    },
    {
        "title": "行业相对财务质量漂移簇",
        "cluster": "industry_relative_financial_quality_drift",
        "vector": [1, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1],
        "data": "DmgrPwang_industry_equ_fancy_factors or equ_factor_pq/growth + forecast/consensus quality fields + daily price; no Interval5m",
        "mechanism": "use one industry-relative quality or growth field as state and compare with own forecast/consensus drift",
        "avoid": "raw industry neutralization mimic or broad sector bet",
    },
    {
        "title": "指数权重压力与个股迟滞簇",
        "cluster": "index_weight_pressure_lagged_stock_response",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1],
        "data": "DmgrWbai_AIndexCSI500Weight/CSI1000Weight + daily OHLCV/amount or Dmgr_MktRet; no Interval5m",
        "mechanism": "treat index-weight membership or pressure as market-flow state and find stocks with lagged price/liquidity response",
        "avoid": "static index membership dummy",
    },
    {
        "title": "市场收益状态下的个股反应非对称簇",
        "cluster": "market_return_state_stock_asymmetry",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1],
        "data": "Dmgr_MktRet/aindexeodprices + daily OHLCV/amount + optional equ_factor_return beta; no Interval5m",
        "mechanism": "condition stock daily reaction on market return state and beta/risk field, then test asymmetric overreaction",
        "avoid": "plain market beta or raw market return timing",
    },
    {
        "title": "高频日派生表间不一致簇",
        "cluster": "hf_daily_derivative_table_disagreement",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],
        "data": "two related hf_daily_der_table_* fields + daily price/amount; no raw 5m tensors",
        "mechanism": "compare one high-frequency daily liquidity/volatility derivative with another related derivative and price response",
        "avoid": "using many hf_daily fields as a black-box blend",
    },
    {
        "title": "Fancy因子簇状态与日频错配簇",
        "cluster": "fancy_factor_state_daily_mismatch",
        "vector": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0, 1, 1],
        "data": "equ_fancy_factors_table1-10 one field + daily OHLCV/amount; no Interval5m",
        "mechanism": "use one fancy-factor state as a compressed signal and test whether daily price or liquidity contradicts it",
        "avoid": "stacking multiple fancy tables or outputting a single opaque field rank",
    },
    {
        "title": "H2L因子期限结构与回撤簇",
        "cluster": "h2l_term_structure_drawdown",
        "vector": [1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1],
        "data": "equ_h2l_factor_t1/t2/t3/t4 + daily high/low/close; no Interval5m",
        "mechanism": "compare short-horizon and long-horizon high-to-low style fields and relate the term-structure to daily drawdown",
        "avoid": "one H2L field rank without term-structure contrast",
    },
    {
        "title": "换手拥挤与基本面质量冲突簇",
        "cluster": "turnover_crowding_fundamental_quality_conflict",
        "vector": [1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1],
        "data": "equ_factor_volume turnover/OBV/VOL fields + equ_factor_pq or ashareincome quality field + daily OHLCV; no Interval5m",
        "mechanism": "find cases where liquidity crowding disagrees with one fundamental quality field and price has overreacted",
        "avoid": "pure turnover reversal or pure quality rank",
    },
    {
        "title": "预期分歧覆盖率与稳定性簇",
        "cluster": "expectation_coverage_stability_mispricing",
        "vector": [1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1],
        "data": "forecast/consensus coverage or stability proxies from available fields + daily OHLCV/amount; no Interval5m",
        "mechanism": "use forecast/consensus availability, stability, or disagreement as confidence state and compare with daily pricing",
        "avoid": "coverage mask only or multi-field confidence score",
    },
]


def _direction_distance(left: list[int], right: list[int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5


def _profile_uses_forecast(profile: dict) -> bool:
    vector = profile.get("vector", [])
    return any(
        DIRECTION_VECTOR_AXES.index(axis) < len(vector)
        and int(vector[DIRECTION_VECTOR_AXES.index(axis)]) != 0
        for axis in FORECAST_DIRECTION_AXES
    )


def _validate_direction_vectors() -> None:
    expected = len(DIRECTION_VECTOR_AXES)
    for item in DIRECTION_CANDIDATES:
        vector = item.get("vector", [])
        if len(vector) != expected:
            raise ValueError(
                f"direction vector length mismatch for {item.get('cluster')}: "
                f"got {len(vector)} expected {expected}"
            )


def _diversified_direction_profiles() -> list[dict]:
    if not DIRECTION_CANDIDATES:
        return []
    _validate_direction_vectors()
    remaining = [dict(item) for item in DIRECTION_CANDIDATES]
    ordered = [remaining.pop(0)]
    while remaining:
        best_idx = max(
            range(len(remaining)),
            key=lambda idx: min(
                _direction_distance(remaining[idx]["vector"], chosen["vector"])
                for chosen in ordered
            ),
        )
        ordered.append(remaining.pop(best_idx))
    return ordered


DIRECTION_PROFILES = [
    profile
    for profile in _diversified_direction_profiles()
    if FORECAST_DATA_ENABLED or not _profile_uses_forecast(profile)
]
NEXT_DIRECTION_JSON = Path(
    os.environ.get("FACTOR_MINER_NEXT_DIRECTION_JSON", str(REPO_ROOT / "output" / "next_factor_direction.json"))
)


def _load_next_direction_profile() -> list[dict]:
    if os.environ.get("FACTOR_MINER_USE_DYNAMIC_DIRECTION", "true").lower() in {"0", "false", "no"}:
        return []
    if not NEXT_DIRECTION_JSON.exists():
        return []
    try:
        data = json.loads(NEXT_DIRECTION_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    vector = data.get("vector", {})
    if isinstance(vector, dict):
        vector_list = [int(vector.get(axis, 0)) for axis in DIRECTION_VECTOR_AXES]
    else:
        vector_list = [int(value) for value in vector]
    if len(vector_list) != len(DIRECTION_VECTOR_AXES):
        return []
    if not FORECAST_DATA_ENABLED and any(
        vector_list[DIRECTION_VECTOR_AXES.index(axis)] for axis in FORECAST_DIRECTION_AXES
    ):
        return []
    profile = dict(data)
    profile["idx"] = 0
    profile["vector"] = vector_list
    profile.setdefault("title", "动态下一轮特征向量方向")
    profile.setdefault("cluster", "dynamic_next_direction")
    profile.setdefault("data", "")
    profile.setdefault("mechanism", "")
    profile.setdefault("avoid", "")
    return [profile]


DYNAMIC_DIRECTION_PROFILES = _load_next_direction_profile()
if DYNAMIC_DIRECTION_PROFILES:
    seen_direction_keys = {
        tuple(profile.get("vector", []))
        for profile in DYNAMIC_DIRECTION_PROFILES
    }
    DIRECTION_PROFILES = DYNAMIC_DIRECTION_PROFILES + [
        profile for profile in DIRECTION_PROFILES
        if tuple(profile.get("vector", [])) not in seen_direction_keys
    ]
DIRECTIONS = [profile["title"] for profile in DIRECTION_PROFILES]

RESEARCH_REPORT_GUIDANCE = """\
External research guidance for lower-correlation intraday factor design:
- Intraday overreaction/reversal: A-share intraday returns often contain reversal information. Prefer conditional reversal signals that depend on market breadth, bar-level excess return, or time-of-day state, not plain full-day return reversal.
- Momentum-pulse idea: compare each stock's 5m return with same-bar cross-sectional market movement; focus on bars where breadth is one-sided or the cross-section has skewed overreaction.
- Volume-surge volatility idea: split the day by stock-specific 5m amount peaks or amount-share regimes, then compare return volatility between high-attention and quiet segments.
- High-price versus low-price trading concentration: use amount-weighted close location, amount-weighted price skewness, or price/amount entropy to detect whether trading concentrates near local highs or lows.
- Intraday volume W-shape: model abnormal opening, afternoon-open, and closing amount shares relative to each stock's recent own history; avoid raw amount level.
- Price-volume matching: use detrended price-location versus amount-share correlation, weighted rank, entropy, or mismatch. High price-volume co-movement can indicate crowded high-price trading and later reversal.

Anti-correlation design rules:
- Do not build another broad amount autocorrelation, simple amo-ratio, generic VWAP distance, plain intraday momentum, plain skew/kurtosis, or one-window volatility factor.
- Every candidate must have an orthogonalizing twist: event-time segmentation, market-breadth conditioning, stock-own-history demeaning, nonlinear entropy/weighted-skew, or mismatch between price location and amount location.
- Prefer cross-sectional bar state computed inside generate(), such as same-bar breadth or return skew across valid stocks, because installed factors are mostly single-stock time-series structures.
- Combine at most two primitives. A small number of distinct primitives is less correlated than a kitchen-sink weighted sum.
- Avoid parameter-sweep-style formulas: no nested decimal-weight blends such as `0.35 + 0.65 * (0.62 * a + 0.38 * b)`. Use simple masks, ranks, or one interpretable coefficient at most.
- If the priority library target is close to installed structures, reinterpret it through one of the research-guidance mechanisms instead of reproducing the obvious formula.
"""

ALLOWED_DATASETS = {
    "volume",
    "open",
    "close",
    "high",
    "low",
    "amount",
    "Interval5m.open",
    "Interval5m.close",
    "Interval5m.amo",
}

DEFAULT_FORBIDDEN_DATASETS = {
    "DmgrWbai_AIndexCSI1000Weight",
    "DmgrWbai_AIndexCSI500Weight",
    "Dmgr_MktRet/aindexeodprices",
    "equ_factor_trend",
    "equ_h2l_factor_t1",
    "equ_h2l_factor_t2",
    "equ_h2l_factor_t3",
    "equ_h2l_factor_t4",
    "hf_daily_auction_table.RET_PRED",
    "hf_daily_auction_table.RET_PRED_0935",
    "hf_daily_auction_table.STAGE_TWO_LEAVE_RATIO",
}
FORBIDDEN_DATASETS = DEFAULT_FORBIDDEN_DATASETS | {
    item.strip()
    for item in os.environ.get("FACTOR_MINER_FORBIDDEN_DATASETS_EXTRA", "").split(",")
    if item.strip()
}

FORECAST_ALLOWED_DATASETS = {
    f"{table}.{field}"
    for table, fields in FORECAST_TABLE_FIELDS.items()
    for field in fields
}

if FORECAST_DATA_ENABLED:
    ALLOWED_DATASETS = ALLOWED_DATASETS | FORECAST_ALLOWED_DATASETS
if CC_ALL_EXTRA_DATA_ENABLED:
    ALLOWED_DATASETS = ALLOWED_DATASETS | CC_ALL_REGISTERED_DATASETS

XML_TEMPLATE = """\
<gsim>
  <Constants backdays="256" niodatapath="{nio_data_path}" niomapprivate="true" authorWeight="ywang:1.0," time_intensive="false"/>
  <Universe startdate="{start_date}" enddate="{end_date}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"/>

  <Modules>
    <Data id="ALL" module="UmgrAll" path=""/>
    <Data id="ALL_TRD" module="UmgrTrd" path=""/>
    <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"/>
    <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""/>
    <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""/>
    <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""/>
    <Data id="ipo" module="DmgrIPO" dataPath="" path=""/>
    <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"/>
    <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"/>
    <Data id="Interval5m" module="DmgrInterval5m" dataPath="" path=""/>
{forecast_data_modules}

    <Alpha id="{factor_name}mod" module="{py_path}"/>
  </Modules>

  <Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
    <Stats module="StatsSimpleV5" mode="0" tradePrice="close" tax="0." fee="0." slippage="0." printStats="true" dumpPnl="true" pnlDir="{pnl_dir}"/>

    <Alpha id="{factor_name}" module="{factor_name}mod" universeId="ALL_TRD" booksize="20e6" delay="0" ndays="20" dumpAlphaFile="true" dumpAlphaDir="{alpha_dir}" st="20">
      <Description name="tvol" author="tinyKaggleClaw" birthday="{today}" category="price_volume" universe="ALL_TRD" delay="0"/>
      <Operations>
        <Operation module="AlphaOpDecay" days="{decay_days}"/>
        <Operation module="AlphaOpPower" exp="{power_exp}"/>
        <Operation module="AlphaOpIndNeut" group="sector"/>
      </Operations>
    </Alpha>
  </Portfolio>
</gsim>
"""


_RUN_LOG: Path | None = None
_FEEDBACK_POOL: ThreadPoolExecutor | None = None
_FEEDBACK_FUTURES: list[Future] = []
_RUN_STATE: dict = {}
_RUN_STATS: dict[str, int] = {}
_RUN_SIGNATURES: set[str] = set()
_LAST_FEEDBACK_PENDING = 0


def log(msg: str) -> None:
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _RUN_LOG is not None:
        with _RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def ensure_research_notes() -> None:
    (RESEARCH_NOTES_DIR / "archive").mkdir(parents=True, exist_ok=True)
    for subdir in ["rules", "findings", "failures"]:
        (RESEARCH_NOTES_DIR / "knowledge" / subdir).mkdir(parents=True, exist_ok=True)
    template = RESEARCH_NOTES_DIR / "TEMPLATE.md"
    if not template.exists():
        template.write_text(
            """# Factor Mining Research Note

## Objective

## Baseline

## Experiments

## Findings

## Failures

## Next Directions
""",
            encoding="utf-8",
        )
    index = RESEARCH_NOTES_DIR / "knowledge" / "INDEX.md"
    if not index.exists():
        index.write_text(
            """# Factor Mining Knowledge Index

This directory is maintained by tinyKaggleClaw local factor miner.

- `rules/`: validated constraints that future generation should obey.
- `findings/`: useful empirical observations.
- `failures/`: failed paths and duplicate signatures to avoid.
""",
            encoding="utf-8",
        )


def read_tail(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def load_research_knowledge() -> str:
    ensure_research_notes()
    parts: list[str] = []
    for label, pattern in [
        ("Rules", "knowledge/rules/*.md"),
        ("Findings", "knowledge/findings/*.md"),
        ("Failures", "knowledge/failures/*.md"),
        ("Archive", "archive/*.md"),
    ]:
        rows = []
        for path in sorted(RESEARCH_NOTES_DIR.glob(pattern))[-8:]:
            rows.append(f"### {path.relative_to(RESEARCH_NOTES_DIR)}\n{read_tail(path, 4000).strip()}")
        if rows:
            parts.append(f"## {label}\n" + "\n\n".join(rows))
    text = "\n\n".join(parts).strip()
    return text[-KNOWLEDGE_TAIL_CHARS:] if text else "No persistent research knowledge yet."


def topic_note_path() -> Path:
    return RESEARCH_NOTES_DIR / "archive" / f"factor-mine-{RESEARCH_TOPIC}.md"


def append_research_note(row: dict) -> None:
    ensure_research_notes()
    path = topic_note_path()
    if not path.exists():
        path.write_text(
            f"# Factor Mining: {RESEARCH_TOPIC}\n\n## Experiments\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as f:
        f.write(
            f"\n### Iter {row.get('iteration')}: {row.get('factor')}\n\n"
            f"- time: {row.get('time')}\n"
            f"- Sharpe: {row.get('sharpe')}\n"
            f"- ret_pct: {row.get('ret_pct')}\n"
            f"- tvr_pct: {row.get('tvr_pct')}\n"
            f"- accepted: {row.get('accepted')}\n"
            f"- reason: {row.get('reason')}\n"
            f"- max_corr: {row.get('max_corr')}\n"
        )


def record_failure_knowledge(row: dict) -> None:
    reason = str(row.get("reason") or "")
    sharpe = row.get("sharpe")
    ret = row.get("ret_pct")
    if row.get("accepted"):
        finding_path = RESEARCH_NOTES_DIR / "knowledge" / "findings" / "accepted_factors.md"
        with finding_path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n- {row.get('factor')}: Sharpe={sharpe} ret={ret}% "
                f"tvr={row.get('tvr_pct')}% max_corr={row.get('max_corr')}\n"
            )
        return
    if "return_gate_failed" in reason or "gsim failed" in reason or "simsummary failed" in reason:
        failure_path = RESEARCH_NOTES_DIR / "knowledge" / "failures" / "recent_failed_paths.md"
        with failure_path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n- {row.get('factor')}: Sharpe={sharpe} ret={ret}% "
                f"tvr={row.get('tvr_pct')}% reason={reason}\n"
            )


def init_run_tracking(run_dir: Path, args: argparse.Namespace) -> None:
    global _RUN_STATE, _RUN_STATS
    ensure_research_notes()
    _RUN_STATS = {
        "codegen_submitted": 0,
        "codegen_failed": 0,
        "backtest_submitted": 0,
        "backtest_failed": 0,
        "simsummary_failed": 0,
        "return_gate_failed": 0,
        "tvr_gate_failed": 0,
        "corr_gate_failed": 0,
        "accepted": 0,
        "negate_retries": 0,
        "near_miss_rescues": 0,
        "decay_retries": 0,
        "high_corr_rescues": 0,
    }
    _RUN_STATE = {
        "run_dir": str(run_dir),
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "gates": {
            "min_sharpe": MIN_SHARPE,
            "min_ret_pct": MIN_RET,
            "max_tvr_pct": MAX_TVR,
            "max_corr": MAX_CORR,
            "high_corr_rescue_max": HIGH_CORR_RESCUE_MAX,
            "prescreen_enabled": PRESCREEN_ENABLED,
        "prescreen_start_date": PRESCREEN_START_DATE,
        "prescreen_min_sharpe": PRESCREEN_MIN_SHARPE,
        "prescreen_min_ret_pct": PRESCREEN_MIN_RET,
        "prescreen_timeout": PRESCREEN_TIMEOUT,
        },
        "models": {
            "codegen": CODEX_MODEL,
            "discussion": DISCUSSION_MODEL,
            "feedback": FEEDBACK_MODEL,
        },
        "stats": _RUN_STATS,
        "recent_results": [],
        "best": None,
    }
    write_run_status(run_dir)


def inc_stat(name: str, amount: int = 1) -> None:
    _RUN_STATS[name] = _RUN_STATS.get(name, 0) + amount


def write_run_status(run_dir: Path) -> None:
    if not _RUN_STATE:
        return
    _RUN_STATE["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _RUN_STATE["stats"] = _RUN_STATS
    status_json = run_dir / "run_status.json"
    status_json.write_text(json.dumps(_RUN_STATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Factor Mining Run Status",
        "",
        f"- run_dir: `{run_dir}`",
        f"- updated_at: `{_RUN_STATE['updated_at']}`",
        f"- codegen_model: `{CODEX_MODEL}`",
        f"- discussion_model: `{DISCUSSION_MODEL}`",
        "",
        "## Stats",
        "",
    ]
    for key in sorted(_RUN_STATS):
        lines.append(f"- `{key}`: {_RUN_STATS[key]}")
    if _RUN_STATE.get("best"):
        lines.extend(["", "## Best", "", f"```json\n{json.dumps(_RUN_STATE['best'], ensure_ascii=False, indent=2)}\n```"])
    if _RUN_STATE.get("recent_results"):
        lines.extend(["", "## Recent Results", ""])
        for row in _RUN_STATE["recent_results"][-20:]:
            lines.append(
                f"- iter `{row.get('iteration')}` `{row.get('factor')}` "
                f"Sharpe={row.get('sharpe')} ret={row.get('ret_pct')}% "
                f"tvr={row.get('tvr_pct')}% reason={row.get('reason')}"
            )
    diag_path = run_dir / "backtest_failure_diagnostics.csv"
    if diag_path.exists():
        try:
            with diag_path.open("r", encoding="utf-8", newline="") as f:
                diag_rows = list(csv.DictReader(f))
        except Exception:
            diag_rows = []
        if diag_rows:
            lines.extend(["", "## Backtest Failure Diagnostics", ""])
            for row in diag_rows[-20:]:
                lines.append(
                    f"- iter `{row.get('iteration')}` `{row.get('factor')}` "
                    f"type={row.get('failure_type')} rc={row.get('rc')} "
                    f"last_date={row.get('last_date') or '-'} summary={row.get('summary')}"
                )
    (run_dir / "run_status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_research_report(run_dir: Path) -> None:
    rows: list[dict] = []
    results_path = run_dir / "results.jsonl"
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    accepted = [row for row in rows if row.get("accepted")]
    numeric = [row for row in rows if row.get("sharpe") is not None]
    best = sorted(numeric, key=lambda row: row.get("sharpe") or -999, reverse=True)[:10]
    rescue_rows = []
    rescue_path = run_dir / "rescue_candidates.csv"
    if rescue_path.exists():
        with rescue_path.open("r", encoding="utf-8", newline="") as f:
            rescue_rows = list(csv.DictReader(f))
    lines = [
        "# Factor Mining Research Report",
        "",
        f"- run_dir: `{run_dir}`",
        f"- generated_at: `{dt.datetime.now().isoformat(timespec='seconds')}`",
        f"- research_notes: `{RESEARCH_NOTES_DIR}`",
        "",
        "## Summary",
        "",
        f"- completed results: {len(rows)}",
        f"- accepted factors: {len(accepted)}",
        f"- rescue candidates: {len(rescue_rows)}",
        "",
        "## Top Factors",
        "",
        "| factor | Sharpe | ret% | tvr% | accepted | reason |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in best:
        lines.append(
            f"| {row.get('factor')} | {row.get('sharpe')} | {row.get('ret_pct')} | "
            f"{row.get('tvr_pct')} | {row.get('accepted')} | {row.get('reason')} |"
        )
    lines.extend(["", "## Rescue Candidates", "", "| factor | labels | Sharpe | ret% | tvr% | reason |", "|---|---|---:|---:|---:|---|"])
    for row in rescue_rows[-20:]:
        lines.append(
            f"| {row.get('factor')} | {row.get('labels')} | {row.get('sharpe')} | "
            f"{row.get('ret_pct')} | {row.get('tvr_pct')} | {row.get('reason')} |"
        )
    lines.extend(
        [
            "",
            "## Knowledge Updated",
            "",
            f"- archive note: `{topic_note_path()}`",
            f"- failures: `{RESEARCH_NOTES_DIR / 'knowledge' / 'failures'}`",
            f"- findings: `{RESEARCH_NOTES_DIR / 'knowledge' / 'findings'}`",
        ]
    )
    (run_dir / "research_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_csv_row(path: Path, row: dict, fields: list[str]) -> None:
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def diagnose_gsim_failure(rc: int, log_path: Path, timeout: int) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    tail = text[-12000:]
    diag = {
        "failure_type": "gsim_failed",
        "exception": "",
        "last_date": "",
        "summary": f"gsim exited rc={rc}",
    }
    dates = re.findall(r"(?m)^(20\d{6})\s+", text)
    if dates:
        diag["last_date"] = dates[-1]
    if rc == 124:
        diag["failure_type"] = "timeout"
        progress = f"; last simulated date={diag['last_date']}" if diag["last_date"] else ""
        diag["summary"] = f"gsim timed out after {timeout}s{progress}; likely factor generate() is too slow"
        return diag
    tb_lines = []
    if "Traceback (most recent call last):" in tail:
        tb = tail.split("Traceback (most recent call last):", 1)[-1]
        tb_lines = [line.rstrip() for line in tb.splitlines() if line.strip()]
        if tb_lines:
            diag["exception"] = tb_lines[-1]
            diag["summary"] = tb_lines[-1]
            diag["failure_type"] = "python_exception"
    if "ufunc 'bitwise_and' not supported" in tail or "ufunc 'bitwise_or' not supported" in tail:
        diag["failure_type"] = "invalid_numpy_bitwise_op"
        diag["summary"] = "generated factor used an in-place bitwise op on numeric arrays; static validator now rejects this"
    elif "No module named" in tail:
        missing = re.findall(r"No module named ['\"]([^'\"]+)['\"]", tail)
        if missing:
            diag["failure_type"] = "missing_module"
            diag["summary"] = f"missing module: {missing[-1]}"
    elif "KeyError" in tail:
        diag["failure_type"] = "data_key_error"
    return diag


def append_backtest_failure_diagnostic(
    run_dir: Path,
    iteration: int,
    factor_name: str,
    rc: int,
    err: str,
    log_path: Path,
    timeout: int,
) -> None:
    diag = diagnose_gsim_failure(rc, log_path, timeout)
    row = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "iteration": iteration,
        "factor": factor_name,
        "rc": rc,
        "failure_type": diag.get("failure_type", ""),
        "last_date": diag.get("last_date", ""),
        "summary": diag.get("summary", ""),
        "exception": diag.get("exception", ""),
        "log_path": str(log_path),
        "err": err,
    }
    append_csv_row(
        run_dir / "backtest_failure_diagnostics.csv",
        row,
        ["time", "iteration", "factor", "rc", "failure_type", "last_date", "summary", "exception", "log_path", "err"],
    )
    md_path = run_dir / "backtest_failure_diagnostics.md"
    exists = md_path.exists()
    with md_path.open("a", encoding="utf-8") as f:
        if not exists:
            f.write(
                "# Backtest Failure Diagnostics\n\n"
                "| time | iter | factor | rc | type | last_date | summary | log |\n"
                "|---|---:|---|---:|---|---|---|---|\n"
            )
        f.write(
            "| {time} | {iteration} | {factor} | {rc} | {failure_type} | {last_date} | {summary} | {log_path} |\n".format(
                **{k: str(v).replace("|", "/") for k, v in row.items()}
            )
        )


def fmt_metric(value: object, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def append_rescue_log(run_dir: Path, row: dict) -> None:
    path = run_dir / "rescue_log.md"
    exists = path.exists()
    with path.open("a", encoding="utf-8") as f:
        if not exists:
            f.write(
                "# Rescue Log\n\n"
                f"- return gate: Sharpe >= {MIN_SHARPE}, ret >= {MIN_RET}%\n"
                f"- tvr gate: tvr < {MAX_TVR}%\n"
                f"- corr rescue range: {MAX_CORR} <= max_corr <= {HIGH_CORR_SKIP_RESCUE_MAX}\n"
                f"- corr skip: max_corr > {HIGH_CORR_SKIP_RESCUE_MAX}\n\n"
                "| time | iter | factor | action | labels | Sharpe | ret | tvr | max_corr | reason |\n"
                "|---|---:|---|---|---|---:|---:|---:|---:|---|\n"
            )
        f.write(
            "| {time} | {iteration} | {factor} | {action} | {labels} | {sharpe} | {ret} | {tvr} | {max_corr} | {reason} |\n".format(
                time=row.get("time", ""),
                iteration=row.get("iteration", ""),
                factor=row.get("factor", ""),
                action=row.get("action", ""),
                labels=row.get("labels", ""),
                sharpe=fmt_metric(row.get("sharpe")),
                ret=fmt_metric(row.get("ret_pct"), "%"),
                tvr=fmt_metric(row.get("tvr_pct"), "%"),
                max_corr=fmt_metric(row.get("max_corr")),
                reason=str(row.get("reason", "")).replace("|", "/"),
            )
        )


def classify_rescue_candidates(metrics: dict | None, install_status: dict | None = None) -> list[str]:
    if not metrics:
        return []
    sharpe = metrics.get("sharpe")
    ret = metrics.get("ret_pct")
    tvr = metrics.get("tvr_pct")
    labels: list[str] = []
    if sharpe is not None and ret is not None:
        if MIN_SHARPE - RESCUE_SHARPE_MARGIN <= sharpe < MIN_SHARPE and ret >= MIN_RET:
            labels.append("near_sharpe")
        if sharpe >= MIN_SHARPE and MIN_RET - RESCUE_RET_MARGIN <= ret < MIN_RET:
            labels.append("near_return")
        if sharpe <= -NEGATE_RETRY_MIN_ABS_SHARPE and ret <= -NEGATE_RETRY_MIN_ABS_RET:
            labels.append("negate")
        if ret >= MIN_RET and sharpe < MIN_SHARPE:
            labels.append("high_ret_low_sharpe")
    if ret is not None and tvr is not None and ret >= MIN_RET and tvr >= MAX_TVR:
        labels.append("high_tvr_decay")
    max_corr = (install_status or {}).get("max_corr")
    if max_corr is not None and MAX_CORR <= max_corr <= HIGH_CORR_SKIP_RESCUE_MAX:
        labels.append("high_corr_decorrelate")
    return labels


def record_result(
    run_dir: Path,
    iteration: int,
    factor_name: str,
    metrics: dict | None,
    err: str | None,
    install_status: dict | None,
) -> None:
    row = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "iteration": iteration,
        "factor": factor_name,
        "sharpe": None if metrics is None else metrics.get("sharpe"),
        "ret_pct": None if metrics is None else metrics.get("ret_pct"),
        "tvr_pct": None if metrics is None else metrics.get("tvr_pct"),
        "dd_pct": None if metrics is None else metrics.get("dd_pct"),
        "fitness": None if metrics is None else metrics.get("fitness"),
        "accepted": bool((install_status or {}).get("accepted")),
        "reason": err or (install_status or {}).get("reason", ""),
        "max_corr": (install_status or {}).get("max_corr"),
    }
    append_research_note(row)
    record_failure_knowledge(row)
    append_jsonl(run_dir / "results.jsonl", row)
    append_csv_row(
        run_dir / "results.csv",
        row,
        ["time", "iteration", "factor", "sharpe", "ret_pct", "tvr_pct", "dd_pct", "fitness", "accepted", "reason", "max_corr"],
    )
    labels = classify_rescue_candidates(metrics, install_status)
    if labels:
        candidate = dict(row)
        candidate["labels"] = ",".join(labels)
        append_csv_row(
            run_dir / "rescue_candidates.csv",
            candidate,
            ["time", "iteration", "factor", "labels", "sharpe", "ret_pct", "tvr_pct", "dd_pct", "fitness", "reason", "max_corr"],
        )
        log_row = dict(candidate)
        log_row["action"] = "TODO"
        append_rescue_log(run_dir, log_row)
    elif row.get("max_corr") is not None and row.get("max_corr") > HIGH_CORR_SKIP_RESCUE_MAX:
        log_row = dict(row)
        log_row["action"] = "SKIP"
        log_row["labels"] = "corr_too_high"
        log_row["reason"] = (
            f"max_corr={row.get('max_corr'):.4f} > {HIGH_CORR_SKIP_RESCUE_MAX}; "
            "too close to existing factor, no rescue"
        )
        append_rescue_log(run_dir, log_row)
    _RUN_STATE.setdefault("recent_results", []).append(row)
    _RUN_STATE["recent_results"] = _RUN_STATE["recent_results"][-50:]
    if metrics and metrics.get("sharpe") is not None:
        best = _RUN_STATE.get("best")
        if best is None or metrics.get("sharpe") > best.get("sharpe", -999):
            _RUN_STATE["best"] = row
    write_run_status(run_dir)
    write_research_report(run_dir)


def direction_file() -> Path:
    return RUN_ROOT / "direction_idx.txt"


def next_direction() -> tuple[int, str]:
    path = direction_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        idx = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        idx = 0
    normalized_idx = idx % len(DIRECTIONS)
    path.write_text(str((normalized_idx + 1) % len(DIRECTIONS)), encoding="utf-8")
    return normalized_idx, DIRECTIONS[normalized_idx]


def direction_feature_profile(direction_idx: int) -> dict:
    if not DIRECTION_PROFILES:
        return {}
    return DIRECTION_PROFILES[direction_idx % len(DIRECTION_PROFILES)]


def direction_feature_block(direction_idx: int) -> str:
    profile = direction_feature_profile(direction_idx)
    if not profile:
        return "No structured direction feature vector is available."
    active_axes = [
        axis
        for axis, value in zip(DIRECTION_VECTOR_AXES, profile.get("vector", []))
        if value
    ]
    vector_text = ", ".join(
        f"{axis}={value}"
        for axis, value in zip(DIRECTION_VECTOR_AXES, profile.get("vector", []))
    )
    return f"""Direction feature-vector cluster:
- cluster: {profile.get("cluster", "")}
- active data/category axes: {", ".join(active_axes)}
- full vector: {vector_text}
- primary data contract: {profile.get("data", "")}
- mechanism constraint: {profile.get("mechanism", "")}
- avoid: {profile.get("avoid", "")}"""


def read_recent_feedback(run_dir: Path) -> str:
    path = run_dir / "agent_feedback_memory.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-FEEDBACK_TAIL_CHARS:].strip()


def summarize_alpha_source(path: Path, max_chars: int = 1400) -> str:
    source = path.read_text(encoding="utf-8", errors="ignore")
    datasets = sorted(set(re.findall(r"dr\.getData\(\s*['\"]([^'\"]+)['\"]\s*\)", source)))
    operations = []
    for line in source.splitlines():
        stripped = line.strip()
        if any(key in stripped for key in ("Interval5m", "amount", "volume", "open", "close", "high", "low", "self.alpha")):
            operations.append(stripped)
    brief = "\n".join(dict.fromkeys(operations))[:max_chars]
    return f"- {path.parent.name}: datasets={datasets}\n  source sketch:\n{brief}"


def load_existing_factor_context(limit: int = 12) -> str:
    rows = []
    for source_path in sorted(WORK_DIR.glob("Alpha*/Alpha*.py")):
        if source_path.parent.name.startswith("."):
            continue
        try:
            rows.append(summarize_alpha_source(source_path))
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return "\n\n".join(rows) if rows else "No active installed factors found."


def load_factors_directory_context(limit: int = 120) -> str:
    csv_path = REPO_ROOT / "output" / "factors_directory" / "factors_directory_zh.csv"
    if not csv_path.exists():
        return "Factors.directory library has not been downloaded yet."
    usable = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                feasible = row.get("feasible_5m", "")
                if feasible not in {"yes", "partial"}:
                    continue
                title = row.get("title", "").replace("Factors.directorynav.menu.open", "").strip()
                url = row.get("url", "")
                slug = url.rstrip("/").split("/")[-1] if url else ""
                name = title or slug
                if name:
                    usable.append(f"- {name} ({feasible}): {slug}")
    except Exception:
        return "Factors.directory context failed to load."
    return "\n".join(usable[:limit]) if usable else "No feasible factors.directory directions classified yet."


def load_factors_directory_items() -> list[dict[str, str]]:
    csv_path = REPO_ROOT / "output" / "factors_directory" / "factors_directory_zh.csv"
    if not csv_path.exists():
        return []
    items = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                feasible = row.get("feasible_5m", "")
                if feasible not in {"yes", "partial"}:
                    continue
                url = row.get("url", "")
                slug = url.rstrip("/").split("/")[-1] if url else ""
                title = row.get("title", "").replace("Factors.directorynav.menu.open", "").strip() or slug
                if slug:
                    items.append({"title": title, "slug": slug, "feasible": feasible, "url": url})
    except Exception:
        return []
    return items


def format_factors_directory_target(item: dict[str, str]) -> str:
    detail = item.get("text", "").strip()
    detail_block = f"\nAvailable source detail excerpt:\n{detail[:1600]}" if detail else ""
    return (
        f"{item['title']} ({item['feasible']}): {item['slug']}\n"
        f"Source: {item['url']}\n"
        "Task: reproduce the economic idea using one clean data family. Use available 5m open/close/amo, "
        "daily OHLCV/amount, or AI earnings forecast plus daily OHLCV/amount, but do not mix forecast datasets "
        "with 5m bars unless the target truly requires it. "
        "If original fields are unavailable, build a faithful proxy and state that proxy in the agent discussion."
        f"{detail_block}"
    )


def has_strict_replication_target() -> bool:
    return FACTOR_LIBRARY_REPLICATION_BIAS and bool(FACTOR_LIBRARY_TARGET_SLUG)


def strict_replication_target_text() -> str:
    if not has_strict_replication_target():
        return ""
    return format_factors_directory_target(
        {
            "title": FACTOR_LIBRARY_TARGET_TITLE or FACTOR_LIBRARY_TARGET_SLUG,
            "slug": FACTOR_LIBRARY_TARGET_SLUG,
            "feasible": "forced",
            "url": FACTOR_LIBRARY_TARGET_URL,
            "text": FACTOR_LIBRARY_TARGET_TEXT,
        }
    )


def iteration_target_log(direction_idx: int, direction: str) -> str:
    if has_strict_replication_target():
        title = FACTOR_LIBRARY_TARGET_TITLE or FACTOR_LIBRARY_TARGET_SLUG
        return f"replication target: {FACTOR_LIBRARY_TARGET_SLUG} ({title})"
    return f"direction [{direction_idx}]: {direction}"


def write_run_direction_family(run_dir: Path, direction_idx: int, direction: str) -> None:
    if has_strict_replication_target():
        text = f"""# Run Direction Family

Strict factors.directory replication target:
{strict_replication_target_text()}
"""
    else:
        text = f"""# Run Direction Family

- direction_idx: {direction_idx}
- direction: {direction}
- one_family_per_run: {ONE_FAMILY_PER_RUN}

{direction_feature_block(direction_idx)}
"""
    (run_dir / "direction_family.md").write_text(text, encoding="utf-8")


def select_factors_directory_target(iteration: int, direction_idx: int) -> str:
    if not FACTOR_LIBRARY_REPLICATION_BIAS:
        return "No specific library target; use the library only as background."
    if FACTOR_LIBRARY_TARGET_SLUG:
        return strict_replication_target_text()
    items = load_factors_directory_items()
    if not items:
        return "Factors.directory target unavailable; use the general direction."
    idx = (iteration - 1 + direction_idx * 17) % len(items)
    item = items[idx]
    return format_factors_directory_target(item)


def forecast_dataset_prompt_block() -> str:
    lines = []
    for table, fields in FORECAST_TABLE_FIELDS.items():
        period = "quarterly arrays are (date, 12, stock)" if table.endswith("_quarter") else "annual arrays are (date, 3, stock)"
        lines.append(f"- {table} ({period}): " + ", ".join(f"'{table}.{field}'" for field in fields))
    return "\n".join(lines)


def forbidden_dataset_prompt_block() -> str:
    return ", ".join(f"'{dataset}'" for dataset in sorted(FORBIDDEN_DATASETS))


def cc_all_dataset_prompt_block(max_fields_per_table: int = 14) -> str:
    if not CC_ALL_EXTRA_DATA_ENABLED:
        return "Additional cc_all datasets are disabled."
    priority = [
        "AShareMoneyFlow",
        "ashareconsensusrollingdata_FY0",
        "ashareconsensusrollingdata_FY1",
        "ashareconsensusrollingdata_FY2",
        "ashareconsensusrollingdata_FY3",
        "hf_daily_auction_table",
        "hf_daily_der_table_1",
        "hf_daily_der_table_2",
        "equ_factor_return",
        "equ_factor_volume",
        "equ_factor_growth",
        "equ_factor_pq",
        "equ_factor_derive",
        "equ_factor_trend",
        "ashareincome",
        "asharebalancesheet",
        "asharecashflow",
    ]
    tables = [table for table in priority if table in CC_ALL_REGISTERED_TABLES]
    tables.extend(
        table for table in sorted(CC_ALL_MODULE_PATHS)
        if table not in tables and table not in FORECAST_TABLE_FIELDS
    )
    lines = [
        (
            f"Registered cc_all data tables: {len(CC_ALL_REGISTERED_TABLES)} tables, "
            f"{len(CC_ALL_REGISTERED_DATASETS)} table-qualified fields. "
            "Use table-qualified names exactly as shown, e.g. dr.getData('equ_factor_return.Beta20').data."
        )
    ]
    missing = sorted(set(CC_ALL_TABLE_FIELDS) - CC_ALL_REGISTERED_TABLES - UNIVERSE_ONLY_TABLES)
    if missing:
        lines.append(
            "Tables present on disk but not XML-registered because no loadable Dmgr module was found: "
            + ", ".join(missing[:20])
        )
    for table in tables[:48]:
        fields = CC_ALL_TABLE_FIELDS.get(table, ())
        shown = fields[:max_fields_per_table]
        suffix = f", ... (+{len(fields) - len(shown)} more)" if len(fields) > len(shown) else ""
        lines.append(f"- {table}: " + ", ".join(f"'{_cc_all_dataset_name(table, field)}'" for field in shown) + suffix)
    return "\n".join(lines)


def build_factor_prompt(
    class_name: str,
    direction_idx: int,
    direction: str,
    iteration: int,
    *,
    discussion: str = "",
    recent_feedback: str = "",
) -> str:
    avoid = ", ".join(DIRECTIONS[max(0, direction_idx - 2):direction_idx + 1])
    feature_block = direction_feature_block(direction_idx)
    if has_strict_replication_target():
        target_block = f"""Strict factors.directory replication target:
{strict_replication_target_text()}

This is a strict replication/adaptation task. The factors.directory target above overrides any generic direction bucket. Do not switch to VWAP, price pressure, amount concentration, reversal, or other generic intraday themes unless they are a faithful proxy for this target."""
        diversity_block = """Strict replication requirement:
- Reproduce or faithfully adapt the priority factors.directory target using one clean data family: 5m open/close/amo, daily OHLCV/amount, or AI earnings forecast plus daily OHLCV/amount. Do not mix forecast datasets with 5m bars unless the target truly requires it.
- Keep the target's core economic intuition and expected sign visible in variable names and comments.
- If original fields are unavailable, use the closest available proxy and keep the proxy narrow.
- Do not use the generic direction bucket as an independent idea source.
- Do not generate a fresh unrelated factor for diversity; diversity is limited to implementation variants of the same target."""
    else:
        target_block = f"""Research target for this candidate:
- Iteration: {iteration}
- Direction index: {direction_idx}
- Direction: {direction}

{feature_block}"""
        diversity_block = f"""Diversity requirement:
- Create a fresh idea from the prompt. Do not reuse a fixed template.
- Treat the direction feature vector as the only hard research-family anchor: the candidate must use this family's active data/category axes and satisfy its mechanism constraint, but it does not need to follow the examples or wording in this prompt literally.
- Think broadly within the family. You may invent a new microstructure interpretation, timing split, conditioning variable, nonlinear statistic, or sign hypothesis if it still fits the selected feature-vector family and available gsim data.
- Use factors.directory, research-report guidance, and prior failures only as background inspiration/risk checks, not as templates to reproduce.
- Avoid merely recombining these recent themes: {avoid}.
- Before writing code, compare the candidate mechanism against "Active installed factor structures to avoid".
- Do not generate another variant of an installed amount/autocorrelation/skew/kurtosis/amo-ratio family unless the primitive is materially different.
- If the idea is close to an installed factor, change at least one core primitive, conditioning regime, and horizon; a simple sign flip, decay change, rank rescale, or weight tweak is not enough.
- Explicitly choose one research-guidance mechanism and make it visible in variable names, e.g. breadth-conditioned overreaction, amount-peak segmentation, weighted price-location entropy, or price/amount mismatch.
- Use stock-own-history normalization for amount-share or volatility primitives whenever possible; raw amount level and raw turnover-like ratios are too correlated with existing factors.
- Use event-time bars selected by amount peaks, breadth states, or abnormal amount shares instead of fixed first/last half windows unless the target specifically requires fixed time.
- The final raw signal should not be a simple linear combination of common intraday return, amount share, and volatility. Include one nonlinear distribution statistic or one conditional mask.
- Avoid scan-parameter overfitting: do not tune several small decimal coefficients inside one expression, do not use nested weighted gates, and do not rescue a factor by adding a calibrated-looking blend of range/liquidity/volatility gates.
- Do not prefer reproducing the factors.directory target in generic mining mode. Only borrow from it if it naturally fits the selected family better than a new idea.
- Avoid paths marked in persistent failures. If a similar hypothesis appears in failures, change the mechanism materially or choose another direction.
- Obey persistent rules unless this prompt gives a stronger hard contract.
- Prefer one coherent hypothesis with a distinctive microstructure mechanism, for example timing of liquidity, price recovery path, order-flow imbalance proxy, volatility compression/expansion, or day-to-day conditioning.
- Make the implementation robust enough for gsim backtest, even if the alpha idea is exploratory."""
    discussion_block = discussion.strip() or "No prior discussion was produced."
    feedback_block = recent_feedback.strip() or "No prior backtest feedback is available for this run yet."
    knowledge_block = load_research_knowledge()
    existing_factor_block = load_existing_factor_context()
    factors_directory_block = load_factors_directory_context()
    factors_directory_target = select_factors_directory_target(iteration, direction_idx)
    forecast_contract = ""
    if FORECAST_DATA_ENABLED:
        forecast_contract = f"""\
AI earnings forecast data is enabled for this run. Treat these datasets as a first-class data family, but keep the factor clean: forecast-based factors should use forecast data plus at most one simple daily OHLCV/amount confirmation. Do not mix forecast datasets with Interval5m.* unless the primary data contract explicitly says so.
- Available forecast fields:
{forecast_dataset_prompt_block()}
- Use the period arrays to understand the second dimension: annual arrays are (date, 3, stock), quarterly arrays are (date, 12, stock).
- Simple forecast primitives only: nearest-period revision versus recent history, annual forecast slope, quarterly near-vs-far contrast, coverage/stability, or one forecast field against one daily liquidity/price primitive.
- Do not combine many forecast fields. Use one revenue, income statement, balance sheet, cash-flow, finance-ratio, or summary primitive plus at most one daily confirmation or contradiction primitive.
- Do not use Interval5m.open/close/amo in forecast-based factors unless the selected direction explicitly requires it.
- Treat negative or non-finite forecast values carefully; avoid log unless values are strictly positive.
"""
    cc_all_contract = ""
    if CC_ALL_EXTRA_DATA_ENABLED:
        cc_all_contract = f"""\
Additional /cache/data/cc_all datasets are enabled and XML-registered when a gsim Dmgr module is available.
{cc_all_dataset_prompt_block()}
- Keep each factor to one clean data family plus at most one simple confirmation field. Do not combine many precomputed factors.
- Prefer economically interpretable fields: money flow, analyst consensus, auction/opening state, precomputed risk/volume/growth/quality factors, or raw financial statement fields.
- Table-qualified fields are daily matrices unless explicitly documented as forecast cubes or Interval5m cubes.
"""
    return f"""You are generating one original Chinese A-share intraday quant factor for gsim.

Return ONLY a complete Python source file. Do not use markdown fences. Do not explain.

Runner post-acceptance rule (do not implement this inside the alpha source):
- When a factor reaches install standards, the runner installs it locally and immediately submits its source package to `/mnt/storage/dropbox/hwang/YYYYMMDD/<factor>/`.
- The dropbox package must contain only `.py`, `.xml`, `.md`, and `.json` files; do not submit pnl or alpha dump files.

Hard contract:
- Define exactly one class named {class_name}(AlphaBase).
- Start with:
  from gsim import DataRegistry as dr
  from gsim import AlphaBase
  import numpy as np
- The class must implement __init__(self, cfg) and generate(self, di).
- Use only local gsim data through dr.getData(...).data. Do not import network, pandas, sklearn, scipy, os, pathlib, subprocess, or external services.
- Safe known datasets: 'volume', 'open', 'close', 'high', 'low', 'amount',
  'Interval5m.open', 'Interval5m.close', 'Interval5m.amo'.
  Forecast and cc_all table-qualified fields are also available when their flags are true; see the data contracts below for usable examples.
- Do not use 'Interval5m.volume', 'Interval5m.high', or 'Interval5m.low'; they are not available in this gsim registry.
- Do not use these known-unavailable or wasteful dataset names under any condition: {forbidden_dataset_prompt_block()}.
- Forecast-data run flag: {FORECAST_DATA_ENABLED}. If true, forecast datasets are available as first-class primitives and should be considered equal-status with 5m K-line data.
- Keep the data family clean. Prefer either forecast + daily OHLCV/amount, or pure daily, or pure 5m microstructure. Do not combine forecast datasets with Interval5m data unless the direction's primary data contract explicitly requires both.
- Keep the logic simple: one core primitive and at most one mask/confirmation primitive. Avoid kitchen-sink formulas and multi-field blends.
- In generate, compute day = di - self.delay and write a vector to self.alpha[valid_idx].
- Use self.valid[di] and a liquid/positive-volume mask. Keep NaN/inf guarded.
- Use vectorized numpy over stocks; avoid per-stock Python loops.
- Use intraday 5m bars index 0:42 when using Interval5m data.
- When using Interval5m data, the primary signal must inspect the full visible 42-bar window `0:42` and may then split it into early/mid/late/event segments. Do not build the whole factor from only a tiny fixed slice such as `0:2`, `0:3`, or `0:4`; this often produces no positions in prescreen.
- Keep 5m history light. Do not build expensive rolling rank/correlation tensors over more than 10 trading days of 5m bars.
- Do not use in-place bitwise/logical augmented assignments such as `raw &= ...`, `raw |= ...`, or `mask ^= ...` on numeric arrays. Use `np.where` or boolean masks instead.
- Do not create parameter-sweep-style composite expressions with multiple fitted-looking decimal weights, especially gate formulas like `0.35 + 0.65 * (0.62 * range_gate + 0.38 * liq_gate)`. A gate should be a simple boolean/quantile mask or one clearly interpretable rank transform, not a nested weighted blend.
- If there is insufficient history or fewer than 10 finite stocks, return without writing.
- Cross-sectionally normalize the final raw signal before assigning self.alpha[valid_idx].
- The factor must be delay=0 compatible and must not look into future days.

{forecast_contract}

{cc_all_contract}

Turnover control:
- Avoid extreme noisy turnover, but do not over-smooth the signal.
- For otherwise strong high-TVR candidates, the runner may rescue turnover with XML decay retries. If a single AlphaOpDecay is insufficient, use two consecutive AlphaOpDecay operations as a valid turnover rescue path before rejecting the factor.
- If high TVR comes from a sparse hard event mask whose member stocks change daily, XML decay will usually not help because non-event stocks drop out instead of carrying yesterday's signal. In that case, rescue the source by replacing the hard event-only assignment with a continuous score on a broader liquid universe, or by adding a persistent event-state term before the final cross-sectional normalization.
- Same-day intraday signals are allowed when the economic mechanism is clear.
- If using history, keep it moderate and purposeful; 3-10 trading days is usually enough.
- Prefer signals that survive the XML AlphaOpDecay post-process rather than manually forcing very slow persistence.
- Do not manually set self.delay; rely on AlphaBase/XML delay.

{target_block}

Three-agent pre-generation discussion:
{discussion_block}

Recent per-factor backtest feedback from this run:
{feedback_block}

Persistent research notes and knowledge base:
{knowledge_block}

Failure investigation contract:
- Treat every backtest/codegen/simsummary failure as a source of future constraints, not as random noise.
- If prior feedback or persistent failures mention no-position pnl, unavailable datasets, bad 5m slices, high TVR, or high correlation, avoid repeating the same pattern.
- The runner records investigated failures under research_notes/knowledge; obey those notes before inventing a new candidate.

Research-report design guidance to reduce correlation:
{RESEARCH_REPORT_GUIDANCE}

Active installed factor structures to avoid:
{existing_factor_block}

Factors.directory 5m-feasible idea library, optional background only in generic mining:
{factors_directory_block}

Factors.directory target selected for optional inspiration in generic mining, or strict target in replication mode:
{factors_directory_target}

{diversity_block}
"""


def extract_python_source(response: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", response, flags=re.S)
    if match:
        response = match.group(1)
    response = response.strip()
    start = response.find("from gsim import")
    if start > 0:
        response = response[start:]
    return response.rstrip() + "\n"


def assignment_target_name(target: ast.AST) -> str:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        return assignment_target_name(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return "_".join(assignment_target_name(item) for item in target.elts)
    return ""


def decimal_weight_constants(expr: ast.AST) -> list[float]:
    values = []
    for node in ast.walk(expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            value = float(node.value)
            if 0.05 <= abs(value) <= 1.50 and abs(value - round(value)) > 1.0e-9:
                values.append(value)
    return values


def binop_kinds(expr: ast.AST) -> set[type]:
    return {type(node.op) for node in ast.walk(expr) if isinstance(node, ast.BinOp)}


def rejects_parameter_sweep_expression(target_name: str, expr: ast.AST) -> bool:
    name = target_name.lower()
    if not any(marker in name for marker in ("gate", "raw", "signal", "score", "weight", "blend")):
        return False
    decimals = decimal_weight_constants(expr)
    if len(decimals) < 3:
        return False
    ops = binop_kinds(expr)
    has_additive = ast.Add in ops or ast.Sub in ops
    has_multiplicative = ast.Mult in ops
    return has_additive and has_multiplicative


def validate_agent_source(source: str, class_name: str, response_path: Path) -> None:
    if f"class {class_name}(AlphaBase)" not in source:
        raise RuntimeError(f"agent response missing class {class_name}; response={response_path}")
    if not re.search(r"self\.alpha\s*\[[^\]]+\]\s*=", source):
        raise RuntimeError(f"agent response does not assign self.alpha[...] ; response={response_path}")
    datasets = set(re.findall(r"dr\.getData\(\s*['\"]([^'\"]+)['\"]\s*\)", source))
    forbidden = sorted(datasets & FORBIDDEN_DATASETS)
    if forbidden:
        raise RuntimeError(f"agent response uses forbidden datasets {forbidden}; response={response_path}")
    unknown = sorted(datasets - ALLOWED_DATASETS)
    if unknown:
        raise RuntimeError(f"agent response uses unavailable datasets {unknown}; response={response_path}")
    if any(dataset in datasets for dataset in ("Interval5m.open", "Interval5m.close", "Interval5m.amo")):
        narrow_slices = re.findall(r"\[\s*[^,\]]+\s*,\s*0\s*:\s*([2-4])\s*,", source)
        has_full_visible_slice = bool(re.search(r"\[\s*[^,\]]+\s*,\s*0\s*:\s*42\s*,", source))
        if narrow_slices and not has_full_visible_slice:
            raise RuntimeError(
                "agent response uses only a tiny Interval5m slice 0:"
                f"{min(narrow_slices)} without a primary 0:42 visible-window slice; "
                "use 0:42 as the main window and then split into segments; "
                f"response={response_path}"
            )
    try:
        tree = ast.parse(source, filename=str(response_path))
    except SyntaxError as exc:
        raise RuntimeError(f"agent response is not valid Python: {exc}; response={response_path}") from exc
    bool_name_markers = ("mask", "valid", "ok", "idx", "event", "bar", "finite", "assign", "enough")
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            target_name = assignment_target_name(node.target).lower()
            if not any(marker in target_name for marker in bool_name_markers):
                raise RuntimeError(
                    f"agent response uses unsafe in-place bitwise assignment on `{target_name}` at line {node.lineno}; "
                    f"use np.where/boolean masks instead; response={response_path}"
                )
        if isinstance(node, ast.Assign):
            target_name = "_".join(assignment_target_name(target) for target in node.targets)
            if rejects_parameter_sweep_expression(target_name, node.value):
                raise RuntimeError(
                    f"agent response uses parameter-sweep-style weighted expression for `{target_name}` at line {node.lineno}; "
                    "avoid nested decimal-weight blends/gates and use a simpler interpretable mask/rank transform; "
                    f"response={response_path}"
                )
        if isinstance(node, ast.AnnAssign):
            target_name = assignment_target_name(node.target)
            if node.value is not None and rejects_parameter_sweep_expression(target_name, node.value):
                raise RuntimeError(
                    f"agent response uses parameter-sweep-style weighted expression for `{target_name}` at line {node.lineno}; "
                    "avoid nested decimal-weight blends/gates and use a simpler interpretable mask/rank transform; "
                    f"response={response_path}"
                )
    compile(tree, str(response_path), "exec")


def negate_alpha_source(source: str, class_name: str, negated_class_name: str) -> str:
    source = source.replace(f"class {class_name}(AlphaBase)", f"class {negated_class_name}(AlphaBase)", 1)
    pattern = re.compile(r"(?P<indent>^[ \t]*)self\.alpha\s*\[(?P<idx>[^\]]+)\]\s*=\s*(?P<expr>.+)$", re.M)
    matches = list(pattern.finditer(source))
    if not matches:
        raise RuntimeError("cannot find self.alpha[...] assignment to negate")
    match = matches[-1]
    replacement = f"{match.group('indent')}self.alpha[{match.group('idx')}] = -({match.group('expr').strip()})"
    return source[:match.start()] + replacement + source[match.end():]


def rename_alpha_source(source: str, class_name: str, renamed_class_name: str) -> str:
    renamed = source.replace(f"class {class_name}(AlphaBase)", f"class {renamed_class_name}(AlphaBase)", 1)
    if renamed == source:
        raise RuntimeError(f"cannot find class {class_name}(AlphaBase) to rename")
    return renamed


def run_codex_agent(
    prompt: str,
    response_path: Path,
    log_path: Path,
    timeout: int = AGENT_TIMEOUT,
    model: str | None = None,
    temperature: str | None = None,
) -> str:
    cmd = [
        CODEX_BIN,
        "exec",
        "-m",
        model or CODEX_MODEL,
        "-C",
        str(REPO_ROOT),
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--output-last-message",
        str(response_path),
        "-",
    ]
    if temperature:
        cmd[4:4] = ["-c", f"model_temperature={temperature}"]
    retry_markers = (
        "429 Too Many Requests",
        "403 Forbidden",
        "failed to connect to websocket",
        "exceeded retry limit",
        "usage limit",
    )
    last_rc = 0
    for attempt in range(AGENT_RETRIES + 1):
        mode = "w" if attempt == 0 else "a"
        with log_path.open(mode, encoding="utf-8") as log_file:
            if attempt:
                log_file.write(f"\n[retry {attempt}/{AGENT_RETRIES}]\n")
            result = subprocess.run(
                cmd,
                input=prompt,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        last_rc = result.returncode
        if result.returncode == 0:
            break
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        if attempt >= AGENT_RETRIES or not any(marker in tail for marker in retry_markers):
            raise RuntimeError(f"agent failed rc={result.returncode} log={log_path}")
        jitter = int(hashlib.sha1(str(response_path).encode("utf-8")).hexdigest()[:4], 16) % 17
        time.sleep(AGENT_RETRY_BASE_SLEEP * (attempt + 1) + jitter)
    if last_rc != 0:
        raise RuntimeError(f"agent failed rc={last_rc} log={log_path}")
    return response_path.read_text(encoding="utf-8") if response_path.exists() else ""


def build_discussion_prompt(
    role: str,
    class_name: str,
    direction_idx: int,
    direction: str,
    iteration: int,
    prior_discussion: str,
    recent_feedback: str,
) -> str:
    feature_block = direction_feature_block(direction_idx)
    if has_strict_replication_target():
        target_block = f"""Strict factors.directory replication target:
{strict_replication_target_text()}

This target overrides the generic direction bucket. Discuss only faithful replication/adaptation of this target with available data."""
    else:
        target_block = f"""Target:
- Iteration: {iteration}
- Class name to be generated later: {class_name}
- Direction index: {direction_idx}
- Direction: {direction}

{feature_block}"""
    base = f"""You are one of three agents discussing a new Chinese A-share intraday gsim factor before code generation.

Write concise Chinese notes only. Do not write Python code.

{target_block}

Class name to be generated later: {class_name}

Available data:
- Daily: volume, open, close, high, low, amount
- Intraday 5m: Interval5m.open, Interval5m.close, Interval5m.amo
- AI earnings forecast, separate first-class data family:
{forecast_dataset_prompt_block()}
- Additional cc_all table-qualified daily fields:
{cc_all_dataset_prompt_block(max_fields_per_table=8)}
- Interval5m.volume/high/low are unavailable.
- Clean-data rule: choose one clean family for the candidate. Forecast directions should use forecast + daily OHLCV/amount and should not use Interval5m. 5m directions should not add forecast fields as a mask.
- Simplicity rule: one core primitive and at most one confirmation or mask. Reject complex multi-field blends.

Recent run feedback:
{recent_feedback or "No prior feedback yet."}

Persistent research knowledge:
{load_research_knowledge()}

Active installed factor structures to avoid:
{load_existing_factor_context()}

Research-report design guidance to reduce correlation:
{RESEARCH_REPORT_GUIDANCE}

Factors.directory 5m-feasible idea library, optional background only in generic mining:
{load_factors_directory_context()}

Factors.directory target selected for optional inspiration in generic mining, or strict target in replication mode:
{select_factors_directory_target(iteration, direction_idx)}

Prior agent notes:
{prior_discussion or "No prior notes yet."}
"""
    if role == "researcher":
        return base + """
Role: Agent 1 / factor researcher.
Propose one implementation hypothesis and the expected signal sign. In generic mining mode, anchor the idea to the direction feature-vector cluster and its data/category axes, but think broadly inside that family and do not treat factors.directory as a required target. Do not replace it with a generic VWAP, pressure, reversal, amount, or correlation idea. Explicitly avoid mechanisms close to active installed factors.
Return:
1. Hypothesis
2. Data fields
3. Expected sign
4. Main novelty
"""
    if role == "reviewer":
        return base + """
Role: Agent 2 / gsim implementation reviewer.
Critique Agent 1's idea for implementation risk, future leakage, unavailable data, NaN/shape pitfalls, turnover risk, and whether it is too similar to active installed factors or recent failures. Reject plain amount autocorrelation, generic VWAP distance, fixed-window momentum/reversal, and simple volatility variants.
Return:
1. Keep/change decision
2. Specific implementation constraints
3. Failure modes to avoid
4. Suggested simplification if needed
"""
    return base + """
Role: Agent 3 / final synthesis agent.
Combine Agent 1 and Agent 2 into a final generation brief for the codegen agent. Be concrete enough that the generated factor has one coherent primitive and robust guards. Include one event-time, nonlinear distribution, or market-breadth conditioning device to reduce correlation.
Return:
1. Final mechanism
2. Formula sketch in words, not code
3. Required guards
4. What the codegen agent must avoid
"""


def run_three_agent_discussion(
    class_name: str,
    direction_idx: int,
    direction: str,
    iteration: int,
    iter_dir: Path,
    recent_feedback: str,
) -> str:
    if DISCUSSION_AGENTS <= 0:
        return ""

    roles = ["researcher", "reviewer", "synthesizer"][:DISCUSSION_AGENTS]
    if DISCUSSION_PARALLEL and len(roles) > 1:
        def run_role(item: tuple[int, str]) -> tuple[int, str, str]:
            idx, role = item
            prompt = build_discussion_prompt(role, class_name, direction_idx, direction, iteration, "", recent_feedback)
            prompt_path = iter_dir / f"agent_{idx}_{role}_prompt.md"
            response_path = iter_dir / f"agent_{idx}_{role}_response.md"
            log_path = iter_dir / f"agent_{idx}_{role}.log"
            prompt_path.write_text(prompt, encoding="utf-8")
            response = run_codex_agent(prompt, response_path, log_path, model=DISCUSSION_MODEL)
            return idx, role, response.strip()

        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            futures = [pool.submit(run_role, item) for item in enumerate(roles, start=1)]
            notes_parallel = [future.result() for future in futures]
        notes_parallel.sort(key=lambda item: item[0])
        discussion = "\n\n".join(
            f"## Agent {idx} ({role})\n{text}" for idx, role, text in notes_parallel
        )
        discussion_path = iter_dir / "agent_discussion.md"
        discussion_path.write_text(discussion + "\n", encoding="utf-8")
        return discussion

    notes: list[tuple[str, str]] = []
    for idx, role in enumerate(roles, start=1):
        prior = "\n\n".join(f"## Agent {i} ({name})\n{text}" for i, (name, text) in enumerate(notes, start=1))
        prompt = build_discussion_prompt(role, class_name, direction_idx, direction, iteration, prior, recent_feedback)
        prompt_path = iter_dir / f"agent_{idx}_{role}_prompt.md"
        response_path = iter_dir / f"agent_{idx}_{role}_response.md"
        log_path = iter_dir / f"agent_{idx}_{role}.log"
        prompt_path.write_text(prompt, encoding="utf-8")
        response = run_codex_agent(prompt, response_path, log_path, model=DISCUSSION_MODEL)
        notes.append((role, response.strip()))

    discussion = "\n\n".join(
        f"## Agent {idx} ({role})\n{text}" for idx, (role, text) in enumerate(notes, start=1)
    )
    discussion_path = iter_dir / "agent_discussion.md"
    discussion_path.write_text(discussion + "\n", encoding="utf-8")
    return discussion


def generate_factor_with_agent(
    class_name: str,
    direction_idx: int,
    direction: str,
    iteration: int,
    iter_dir: Path,
    run_dir: Path,
) -> str:
    recent_feedback = read_recent_feedback(run_dir)
    discussion = run_three_agent_discussion(
        class_name, direction_idx, direction, iteration, iter_dir, recent_feedback
    )
    prompt = build_factor_prompt(
        class_name,
        direction_idx,
        direction,
        iteration,
        discussion=discussion,
        recent_feedback=recent_feedback,
    )
    prompt_path = iter_dir / "factor_prompt.md"
    response_path = iter_dir / "agent_response.md"
    agent_log_path = iter_dir / "agent_codegen.log"
    prompt_path.write_text(prompt, encoding="utf-8")
    response = run_codex_agent(prompt, response_path, agent_log_path, temperature=CODEGEN_TEMPERATURE)
    source = extract_python_source(response)
    validate_agent_source(source, class_name, response_path)
    return source


def make_run_dir(seed: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUN_ROOT / f"factor_mining_{seed}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_candidate(run_dir: Path, iteration: int, direction_idx: int, direction: str,
                    start_date: str, end_date: str, factor_prefix: str = "AlphaTinyClaw") -> tuple[int, str, Path, Path, Path]:
    iter_dir = run_dir / f"iter_{iteration:03d}"
    pnl_dir = iter_dir / "pnl"
    pnl_dir.mkdir(parents=True, exist_ok=True)
    factor_name = f"{factor_prefix}D{direction_idx}I{iteration}"
    py_path = iter_dir / f"{factor_name}.py"
    xml_path = iter_dir / "Config.tinyclaw.xml"
    source = generate_factor_with_agent(factor_name, direction_idx, direction, iteration, iter_dir, run_dir)
    signature = ensure_unique_signature(source, factor_name)
    py_path.write_text(source, encoding="utf-8")
    (iter_dir / "source_signature.txt").write_text(signature + "\n", encoding="utf-8")
    xml_path.write_text(
        XML_TEMPLATE.format(
            nio_data_path=NIO_DATA_PATH,
            forecast_data_modules=(
                (FORECAST_DATA_MODULES if FORECAST_DATA_ENABLED else "")
                + (CC_ALL_EXTRA_DATA_MODULES if CC_ALL_EXTRA_DATA_ENABLED else "")
            ),
            start_date=start_date,
            end_date=end_date,
            factor_name=factor_name,
            py_path=py_path,
            pnl_dir=pnl_dir,
            alpha_dir=WORK_DIR / "alpha",
            today=dt.date.today().strftime("%Y%m%d"),
            decay_days=DECAY_DAYS,
            power_exp=1.0,
        ),
        encoding="utf-8",
    )
    return iteration, factor_name, xml_path, pnl_dir / factor_name, iter_dir / "gsim.log"


def run_gsim(xml_path: Path, log_path: Path, timeout: int) -> int:
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [str(GSIM_PYTHON), str(GSIM_RUN), str(xml_path)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return 124


def make_prescreen_job(job: tuple[int, str, Path, Path, Path]) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    prescreen_xml = iter_dir / "Config.tinyclaw.prescreen.xml"
    prescreen_pnl_dir = iter_dir / "pnl_prescreen"
    prescreen_pnl_dir.mkdir(parents=True, exist_ok=True)
    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = re.sub(r'startdate="\d+"', f'startdate="{PRESCREEN_START_DATE}"', xml_text, count=1)
    xml_text = xml_text.replace(str(pnl_file.parent), str(prescreen_pnl_dir))
    prescreen_xml.write_text(xml_text, encoding="utf-8")
    return iteration, factor_name, prescreen_xml, prescreen_pnl_dir / factor_name, iter_dir / "gsim.prescreen.log"


def passes_prescreen(metrics: dict) -> bool:
    sharpe = metrics.get("sharpe")
    ret = metrics.get("ret_pct")
    if sharpe is None or ret is None:
        return False
    positive = sharpe >= PRESCREEN_MIN_SHARPE and ret >= PRESCREEN_MIN_RET
    strong_negative = sharpe <= -PRESCREEN_MIN_SHARPE and ret <= -PRESCREEN_MIN_RET
    return positive or strong_negative


def simsummary(pnl_file: Path) -> str:
    result = subprocess.run(
        [str(GSIM_PYTHON), str(GSIM_SUMMARY), str(pnl_file)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "simsummary failed")
    return result.stdout.strip()


def parse_full_period(summary: str) -> dict[str, float | str | None]:
    lines = [line for line in summary.splitlines() if line.strip()]
    numeric_lines = [line for line in lines if re.match(r"^\d{8}-\d{8}\s+", line)]
    if not numeric_lines:
        raise RuntimeError("no_numeric_summary_row: pnl has no effective long/short positions or no valid return rows")
    full = numeric_lines[-1]
    parts = full.split()
    sharpe_match = re.match(r"([-0-9.]+)", parts[6]) if len(parts) > 6 else None
    ir_match = re.search(r"\(([- 0-9.]+)\)", parts[6]) if len(parts) > 6 else None
    return {
        "date_range": parts[0] if parts else "",
        "pnl_m": float(parts[3]),
        "ret_pct": float(parts[4]),
        "tvr_pct": float(parts[5]),
        "sharpe": float(sharpe_match.group(1)) if sharpe_match else None,
        "ir": float(ir_match.group(1).strip()) if ir_match else None,
        "dd_pct": float(parts[8]),
        "win_pct": float(parts[9]),
        "fitness": float(parts[10]),
        "raw": full,
    }


def passes_return_gate(metrics: dict) -> bool:
    sharpe = metrics.get("sharpe")
    ret = metrics.get("ret_pct")
    return sharpe is not None and ret is not None and sharpe >= MIN_SHARPE and ret >= MIN_RET


def passes_tvr_gate(metrics: dict) -> bool:
    tvr = metrics.get("tvr_pct")
    return tvr is not None and tvr < MAX_TVR


def should_near_miss_rescue(metrics: dict) -> bool:
    sharpe = metrics.get("sharpe")
    ret = metrics.get("ret_pct")
    if sharpe is None or ret is None:
        return False
    near_sharpe = MIN_SHARPE - RESCUE_SHARPE_MARGIN <= sharpe < MIN_SHARPE and ret >= MIN_RET
    near_ret = sharpe >= MIN_SHARPE and MIN_RET - RESCUE_RET_MARGIN <= ret < MIN_RET
    near_both = (
        MIN_SHARPE - RESCUE_SHARPE_MARGIN <= sharpe < MIN_SHARPE
        and MIN_RET - RESCUE_RET_MARGIN <= ret < MIN_RET
    )
    return near_sharpe or near_ret or near_both


def run_bcorr(pnl_file: Path, pnl_dir: Path = PNL_DIR) -> dict | None:
    if not pnl_dir.is_dir() or not any(pnl_dir.iterdir()):
        return None
    result = subprocess.run(
        [str(BCORR_BIN), str(pnl_file), str(pnl_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "bcorr failed")
    out = result.stdout.strip()
    if not out:
        return None
    corr_map = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            if Path(parts[0]).name == pnl_file.name:
                continue
            try:
                corr_map[parts[0]] = float(parts[1])
            except ValueError:
                pass
    if not corr_map:
        return None
    max_name, max_corr = max(corr_map.items(), key=lambda item: item[1])
    return {"max_corr": max_corr, "max_corr_name": max_name, "corr_map": corr_map, "raw": out}


def make_factor_logic_description(source: str) -> str:
    if (
        "industry_leader_momentum_premium" in source
        and "leader_idx" in source
        and "follower_idx" in source
        and "group_id" in source
    ):
        ret_window = "14 日" if "ret_win = 14" in source else "10-14 日"
        leader_cut = "52%" if "cum_share <= 0.52" in source else "约 60%"
        sign_text = (
            "最终输出对标准化信号取负号，因此实盘方向是反向：当组内高成交额 leader 相对 follower 的动量溢价越强时，因子倾向于降低 leader 侧暴露、提高 follower/相对落后侧暴露。"
            if "self.alpha[valid_idx] = -(out[valid_idx])" in source
            else "最终按合成后的标准化信号直接输出。"
        )
        extra_gate = ""
        if "intraday_gate" in source and "late_intensity_rank" in source:
            extra_gate = """
8. 日内去相关门控 `intraday_gate`：
   - 使用当日 5 分钟 `Interval5m.open/close/amo` 的 `0:42` bar。
   - 计算后段成交额占比 `late_share` 和早段成交额占比 `early_share`，用 `late_share - early_share` 做截面排名。
   - 同时计算 5 分钟绝对收益均值 `intraday_abs`，高日内波动会被扣分。
   - `intraday_gate = 0.78 + 0.24 * late_intensity_rank - 0.18 * intraday_abs_rank`，并 clip 到 `[0.64, 1.08]`。
   这个门控保留“成交后移但不过度震荡”的样本，减少与已有成交额自相关/日内形态因子的重合。
"""
        return f"""## 因子逻辑说明

这个因子刻画的是“行业/流动性可比组内，高成交额 leader 相对 follower 的动量溢价是否过度拥挤”。核心假设是：在相似流动性、波动率和成交稳定性的股票组内，如果近期成交额头部股票已经相对尾部股票形成明显动量溢价，这种溢价容易包含拥挤交易或资金追逐，后续更适合作为反向信号处理。

主要计算步骤：

1. 数据依赖：
   - 日频：`volume`、`amount`、`open`、`close`、`high`、`low`。
   - 5 分钟：`Interval5m.open`、`Interval5m.close`、`Interval5m.amo`。
2. 有效股票过滤：
   - 要求当日成交量、成交额、开收盘价有效且大于 0。
   - 要求 `high/low` 有效，且 `high > low`。
   - 如果有效股票少于 10 只则跳过当天。
3. 构造中短期收益 `stock_ret`：
   - 中期收益使用最近 {ret_window} 收盘到当日收盘的收益。
   - 快速收益使用最近 5-6 日收盘到当日收盘的收益。
   - 当前版本用 `0.70 * ret_mid + 0.30 * ret_fast`，让主信号偏中期，但保留短期变化。
4. 构造分组变量：
   - `liq_rank`：过去慢窗口平均成交额的截面排名，代表流动性层级。
   - `vol_rank`：慢窗口日收益波动率排名，代表风险/弹性层级。
   - `stable_rank`：成交额 log 标准差取负后的排名，代表成交稳定性。
   - 三者离散成 `4 * 3 * 2 = 24` 个可比组，后续只在组内比较，避免把不同流动性和风险层级的股票直接混在一起。
5. 组内 leader/follower 切分：
   - 在每个组内按最近信号窗口累计成交额 `cum_amt` 从大到小排序。
   - 累计成交额占比到 {leader_cut} 左右的股票定义为 `leader_idx`，其余定义为 `follower_idx`。
   - 每组至少需要 12 只有效股票，且 leader 至少 3 只、follower 至少 5 只。
6. 构造组内 leader 动量溢价：
   - `leader_avg_return`：leader 组近期平均收益。
   - `follower_avg_return`：follower 组近期平均收益。
   - `industry_leader_momentum_premium = leader_avg_return - follower_avg_return`。
   - `role_signal = leader_flag * industry_leader_momentum_premium`，leader 为 `+1`，follower 为 `-1`。
7. 构造组内个股相对项 `own_excess`：
   - 先用组内中位数和 MAD 对个股 `stock_ret` 做稳健标准化。
   - `own_z` clip 到 `[-2.5, 2.5]`，避免极端收益主导信号。
   - 再乘以流动性/波动门控 `liq_vol_gate`，偏好成交稳定、流动性较好、慢窗口波动不过高的股票。
{extra_gate}9. 合成 raw signal：
   - 主项：`0.82 * role_signal`，保留组内 leader/follower 溢价结构。
   - 辅项：`0.18 * own_excess`，只保留少量个股相对强弱信息，避免退化成普通动量。
   - `dispersion_gate = clip(1.05 - 0.08 * group_dispersion, 0.72, 1.08)`：当组内收益离散度过大、leader 溢价代表性较弱时降低权重。
10. 标准化和输出：
   - 对 raw 做截面 median/MAD 标准化，clip 到 `[-3.8, 3.8]`。
   - 再乘以 `liq_vol_gate` 和可用的日内门控，做二次 z-score 标准化并 clip。
   - {sign_text}

直观解释：它不是简单买行业强势股，也不是简单成交额动量。它先把股票放进可比的流动性/波动/成交稳定性小组，再检查组内“成交额头部股票是否相对尾部股票跑得太多”。如果这种 leader 溢价过强，最终因子按反向处理；同时用日内成交后移、低过度震荡和组内离散度控制来降低与已有成交额自相关类因子的重合。
"""
    if "am_neg_pressure" in source and "pm_repair" in source and "pm_backload" in source:
        return """## 因子逻辑说明

这个因子刻画的是“上午承压后，下午是否出现有成交额支持的修复”。核心假设是：如果股票上午出现下跌压力，但下午价格能够修复，同时成交额从上午向下午迁移，说明日内卖压被吸收，后续有更高概率延续相对强势。

主要计算步骤：

1. 使用当日 5 分钟 `open/close/amo`，取 `0:42` 共 42 根有效 bar。
2. 将日内切成上午段 `0:24` 和下午段 `24:end`，分别计算成交额和价格移动。
3. 构造上午负压 `am_neg_pressure`：只保留上午下跌部分，上午跌得越多，该项越大。
4. 构造下午修复 `pm_repair`：下午收益减去一部分上午上涨项，避免把单纯追涨误判成修复。
5. 构造成交额迁移 `liq_shift`：比较下午成交额和上午成交额占比，并用过去 5 日均值 `norm_shift` 做去偏。
6. 构造下午成交后置 `pm_backload`：比较下午后段和下午早段成交额，识别修复是否发生在更靠后的交易时段。
7. 构造下午压缩项 `pm_compression`：当日下午波动相对过去 5 日下午波动越低，惩罚越小，偏好更平稳的修复。
8. 合成 raw signal：
   - `pm_repair * (0.75 + 2.0 * am_neg_pressure)`：上午跌后下午修复是主信号。
   - `* (1.0 + 0.6 * pm_backload)`：如果成交额更偏下午后段，增强信号。
   - `+ 0.35 * (liq_shift - norm_shift)`：奖励相对历史更明显的下午流动性迁移。
   - `+ 0.18 * pm_compression`：偏好下午修复过程更平稳的股票。
9. 最后使用 MAD/z-score 截面标准化并 clip 到 `[-5, 5]`，再二次标准化后写入 `self.alpha[valid_idx]`。

直观解释：这个因子不是简单买下午上涨，而是寻找“上午有压力、下午被资金承接并平稳修复、且成交额相对后移”的日内结构。它更像是日内卖压吸收与下午再定价效率因子。
"""
    return """## 因子逻辑说明

该因子由 tinyKaggleClaw 自动生成。源码使用本地 gsim 数据构造日内价格/成交额结构信号，并在有效股票截面上做稳健标准化后输出 alpha。

阅读源码时重点关注：

1. `dr.getData(...)` 中声明的数据依赖。
2. `generate()` 中的日内切片、历史窗口和 raw signal 合成。
3. `self.alpha[valid_idx] = ...` 前的 NaN/inf 过滤、截面标准化和 clip 逻辑。
"""


def make_install_readme(
    factor_name: str,
    metrics: dict,
    corr_result: dict | None,
    source: str,
    full_summary: str,
) -> str:
    corr_block = ""
    if corr_result:
        top = sorted(corr_result["corr_map"].items(), key=lambda item: -item[1])[:10]
        rows = "\n".join(f"{name:<50s} {corr:.4f}" for name, corr in top)
        corr_block = f"""
## Correlation At Install

max_corr = {corr_result.get('max_corr', 0.0):.4f}

```
{rows}
```
"""
    return f"""# {factor_name}

Installed by tinyKaggleClaw local factor miner.

{make_factor_logic_description(source)}

## Full-Period Metrics

```
ret={metrics.get('ret_pct', 0):.2f}%  tvr={metrics.get('tvr_pct', 0):.2f}%  sharpe={metrics.get('sharpe', 0):.2f}  dd={metrics.get('dd_pct', 0):.2f}%
```

## Complete simsummary

```
{full_summary.strip() or metrics.get('raw', '')}
```
{corr_block}
## Generated

{dt.date.today().isoformat()}
"""


def source_signature(source: str) -> str:
    datasets = sorted(set(re.findall(r"dr\.getData\(\s*['\"]([^'\"]+)['\"]\s*\)", source)))
    assignments = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", source, flags=re.M)
    alpha_lines = re.findall(r"self\.alpha\s*\[[^\]]+\]\s*=\s*(.+)", source)
    normalized = "\n".join(
        [
            "datasets:" + ",".join(datasets),
            "assignments:" + ",".join(assignments[-40:]),
            "alpha:" + "|".join(line.strip() for line in alpha_lines[-3:]),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def ensure_unique_signature(source: str, factor_name: str) -> str:
    signature = source_signature(source)
    if not DUPLICATE_CHECK_ENABLED:
        _RUN_SIGNATURES.add(signature)
        return signature
    duplicate = [name for name, sig in active_factor_signatures().items() if sig == signature and name != factor_name]
    if signature in _RUN_SIGNATURES:
        duplicate.append("current_run")
    if duplicate:
        failure_path = RESEARCH_NOTES_DIR / "knowledge" / "failures" / "duplicate_signatures.md"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        with failure_path.open("a", encoding="utf-8") as f:
            f.write(f"\n- {factor_name}: signature={signature} duplicate={','.join(duplicate)}\n")
        raise RuntimeError(f"duplicate_factor_signature {signature} existing={','.join(duplicate)}")
    _RUN_SIGNATURES.add(signature)
    return signature


def active_factor_signatures() -> dict[str, str]:
    signatures: dict[str, str] = {}
    for meta in WORK_DIR.glob("Alpha*/factor_meta.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        signature = data.get("source_signature")
        if signature:
            signatures[meta.parent.name] = signature
    return signatures


INSTALLED_FACTOR_LEDGER_FIELDS = [
    "factor",
    "installed_at",
    "source_signature",
    "sharpe",
    "ret_pct",
    "tvr_pct",
    "dd_pct",
    "max_corr",
    "max_corr_name",
    "source_run_dir",
    "factor_dir",
    "source_path",
    "pnl_path",
    "has_meta",
    "ledger_updated_at",
]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, rows: list[dict]) -> None:
    _atomic_write_text(path, json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    tmp.replace(path)


def _installed_factor_row(factor_dir: Path, ledger_time: str) -> dict:
    meta_path = factor_dir / "factor_meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    factor_name = str(meta.get("factor_name") or factor_dir.name)
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    source_path = factor_dir / f"{factor_name}.py"
    pnl_path = PNL_DIR / factor_name
    installed_at = str(meta.get("installed_at") or "")
    if not installed_at:
        installed_at = dt.datetime.fromtimestamp(factor_dir.stat().st_mtime).isoformat(timespec="seconds")
    return {
        "factor": factor_name,
        "installed_at": installed_at,
        "source_signature": meta.get("source_signature", ""),
        "sharpe": metrics.get("sharpe", ""),
        "ret_pct": metrics.get("ret_pct", ""),
        "tvr_pct": metrics.get("tvr_pct", ""),
        "dd_pct": metrics.get("dd_pct", ""),
        "max_corr": meta.get("max_corr", ""),
        "max_corr_name": meta.get("max_corr_name", ""),
        "source_run_dir": meta.get("source_run_dir", ""),
        "factor_dir": str(factor_dir),
        "source_path": str(source_path) if source_path.exists() else "",
        "pnl_path": str(pnl_path) if pnl_path.exists() else "",
        "has_meta": bool(meta),
        "ledger_updated_at": ledger_time,
    }


def sync_installed_factor_ledger() -> list[dict]:
    """Rebuild the installed-factor ledger from the canonical factor directories."""
    ledger_time = dt.datetime.now().isoformat(timespec="seconds")
    rows = [_installed_factor_row(path, ledger_time) for path in sorted(WORK_DIR.glob("Alpha*")) if path.is_dir()]
    rows.sort(key=lambda row: (str(row.get("installed_at", "")), str(row.get("factor", ""))))
    _atomic_write_csv(INSTALLED_FACTOR_LEDGER_CSV, rows, INSTALLED_FACTOR_LEDGER_FIELDS)
    _atomic_write_json(INSTALLED_FACTOR_LEDGER_JSON, rows)
    lines = [
        "# Installed Factor Ledger",
        "",
        f"- updated_at: `{ledger_time}`",
        f"- count: `{len(rows)}`",
        "",
        "| factor | installed_at | Sharpe | ret% | tvr% | max_corr | source |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('factor')} | {row.get('installed_at')} | {row.get('sharpe')} | "
            f"{row.get('ret_pct')} | {row.get('tvr_pct')} | {row.get('max_corr')} | "
            f"{row.get('source_run_dir')} |"
        )
    _atomic_write_text(INSTALLED_FACTOR_LEDGER_MD, "\n".join(lines) + "\n")
    return rows


def submit_installed_factor_to_dropbox(factor_name: str) -> Path:
    """Submit the installed factor source package by install date.

    Dropbox submissions intentionally include only source/config/metadata files:
    .py, .xml, .md, and .json. PnL and alpha dump files stay in the work tree.
    """
    install_dir = WORK_DIR / factor_name
    meta_path = install_dir / "factor_meta.json"
    install_day = dt.datetime.now().strftime("%Y%m%d")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            installed_at = str(meta.get("installed_at") or "")
            if installed_at:
                install_day = installed_at[:10].replace("-", "")
        except Exception:
            pass

    target_dir = DROPBOX_SUBMIT_ROOT / install_day / factor_name
    target_dir.mkdir(parents=True, exist_ok=True)
    allowed_suffixes = {".py", ".xml", ".md", ".json"}
    for child in list(target_dir.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        elif child.suffix not in allowed_suffixes:
            child.unlink()
    for source in sorted(install_dir.iterdir()):
        if source.is_file() and source.suffix in allowed_suffixes:
            shutil.copy2(source, target_dir / source.name)
    return target_dir


def install_factor(job: tuple[int, str, Path, Path, Path], metrics: dict, corr_result: dict | None) -> None:
    _iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    source_path = iter_dir / f"{factor_name}.py"
    source = source_path.read_text(encoding="utf-8", errors="replace") if source_path.exists() else ""
    try:
        full_summary = simsummary(pnl_file)
    except Exception:
        full_summary = str(metrics.get("raw", ""))
    signature = source_signature(source)
    duplicate = [name for name, sig in active_factor_signatures().items() if sig == signature and name != factor_name]
    if duplicate:
        raise RuntimeError(f"source_signature_duplicate {signature} existing={','.join(duplicate)}")

    install_dir = WORK_DIR / factor_name
    tmp_dir = WORK_DIR / f".{factor_name}.installing"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source_path, tmp_dir / f"{factor_name}.py")
    shutil.copy2(xml_path, tmp_dir / "Config.hwang.xml")
    (tmp_dir / f"Readme.Hwang.{factor_name}.md").write_text(
        make_install_readme(factor_name, metrics, corr_result, source, full_summary),
        encoding="utf-8",
    )
    (tmp_dir / "factor_meta.json").write_text(
        json.dumps(
            {
                "factor_name": factor_name,
                "installed_at": dt.datetime.now().isoformat(timespec="seconds"),
                "source_signature": signature,
                "metrics": metrics,
                "max_corr": (corr_result or {}).get("max_corr"),
                "max_corr_name": (corr_result or {}).get("max_corr_name"),
                "source_run_dir": str(iter_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    required = [
        tmp_dir / f"{factor_name}.py",
        tmp_dir / "Config.hwang.xml",
        tmp_dir / f"Readme.Hwang.{factor_name}.md",
        tmp_dir / "factor_meta.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"install_transaction_missing_files {missing}")

    PNL_DIR.mkdir(parents=True, exist_ok=True)
    tmp_pnl = PNL_DIR / f".{factor_name}.installing"
    if pnl_file.exists():
        shutil.copy2(pnl_file, tmp_pnl)
    if install_dir.exists():
        archive_root = WORK_DIR / "factor_retired_duplicate_or_noncompliant_20260512"
        archive_root.mkdir(parents=True, exist_ok=True)
        archive_dir = archive_root / f"{factor_name}.{dt.datetime.now().strftime('%H%M%S')}"
        shutil.move(str(install_dir), str(archive_dir))
    shutil.move(str(tmp_dir), str(install_dir))
    if tmp_pnl.exists():
        final_pnl = PNL_DIR / factor_name
        if final_pnl.exists():
            final_pnl.unlink()
        shutil.move(str(tmp_pnl), str(final_pnl))

    index_path = WORK_DIR / "FACTOR_INDEX.md"
    with index_path.open("a", encoding="utf-8") as f:
        f.write(
            f"\n---\n\n### {factor_name}（tinyKaggleClaw 自动挖掘）\n\n"
            f"`ret={metrics.get('ret_pct', 0):.2f}%`  "
            f"`tvr={metrics.get('tvr_pct', 0):.2f}%`  "
            f"`sharpe={metrics.get('sharpe', 0):.2f}`  "
            f"`max_corr={(corr_result or {}).get('max_corr', 0.0):.3f}`\n\n"
            f"源码：`{factor_name}/{factor_name}.py`\n"
        )
    sync_installed_factor_ledger()
    submit_installed_factor_to_dropbox(factor_name)


def make_decay_job(
    job: tuple[int, str, Path, Path, Path],
    decay_days: int,
) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    decay_name = f"{factor_name}Decay{decay_days}"
    source_path = iter_dir / f"{factor_name}.py"
    decay_source_path = iter_dir / f"{decay_name}.py"
    decay_xml_path = iter_dir / f"Config.tinyclaw.decay{decay_days}.xml"
    decay_log_path = iter_dir / f"gsim.decay{decay_days}.log"
    decay_pnl_dir = iter_dir / f"pnl_decay{decay_days}"
    decay_pnl_dir.mkdir(parents=True, exist_ok=True)

    source = source_path.read_text(encoding="utf-8")
    decay_source = rename_alpha_source(source, factor_name, decay_name)
    validate_agent_source(decay_source, decay_name, decay_source_path)
    decay_source_path.write_text(decay_source, encoding="utf-8")

    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = xml_text.replace(str(source_path), str(decay_source_path))
    xml_text = xml_text.replace(f'{factor_name}mod', f'{decay_name}mod')
    xml_text = xml_text.replace(f'id="{factor_name}"', f'id="{decay_name}"')
    xml_text = xml_text.replace(str(pnl_file.parent), str(decay_pnl_dir))
    xml_text = re.sub(r'(<Operation module="AlphaOpDecay" days=")\d+(")', rf"\g<1>{decay_days}\2", xml_text)
    decay_xml_path.write_text(xml_text, encoding="utf-8")
    return iteration, decay_name, decay_xml_path, decay_pnl_dir / decay_name, decay_log_path


def make_double_decay_job(
    job: tuple[int, str, Path, Path, Path],
    first_decay_days: int,
    second_decay_days: int,
) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    decay_name = f"{factor_name}Decay{first_decay_days}Decay{second_decay_days}"
    source_path = iter_dir / f"{factor_name}.py"
    decay_source_path = iter_dir / f"{decay_name}.py"
    suffix = f"decay{first_decay_days}_decay{second_decay_days}"
    decay_xml_path = iter_dir / f"Config.tinyclaw.{suffix}.xml"
    decay_log_path = iter_dir / f"gsim.{suffix}.log"
    decay_pnl_dir = iter_dir / f"pnl_{suffix}"
    decay_pnl_dir.mkdir(parents=True, exist_ok=True)

    source = source_path.read_text(encoding="utf-8")
    decay_source = rename_alpha_source(source, factor_name, decay_name)
    validate_agent_source(decay_source, decay_name, decay_source_path)
    decay_source_path.write_text(decay_source, encoding="utf-8")

    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = xml_text.replace(str(source_path), str(decay_source_path))
    xml_text = xml_text.replace(f'{factor_name}mod', f'{decay_name}mod')
    xml_text = xml_text.replace(f'id="{factor_name}"', f'id="{decay_name}"')
    xml_text = xml_text.replace(str(pnl_file.parent), str(decay_pnl_dir))
    double_decay_ops = (
        f'<Operation module="AlphaOpDecay" days="{first_decay_days}"/>\n'
        f'        <Operation module="AlphaOpDecay" days="{second_decay_days}"/>'
    )
    xml_text = re.sub(r'<Operation module="AlphaOpDecay" days="\d+"\s*/>', double_decay_ops, xml_text, count=1)
    decay_xml_path.write_text(xml_text, encoding="utf-8")
    return iteration, decay_name, decay_xml_path, decay_pnl_dir / decay_name, decay_log_path


def maybe_run_decay_retry(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    timeout: int,
) -> tuple[tuple[int, str, Path, Path, Path], dict] | None:
    if passes_tvr_gate(metrics):
        return None
    inc_stat("decay_retries")
    _iteration, factor_name, _xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    retry_note = iter_dir / "decay_retry.txt"
    retry_note.write_text(
        f"triggered: {factor_name} Sharpe={metrics.get('sharpe')} "
        f"ret={metrics.get('ret_pct')}% tvr={metrics.get('tvr_pct')}% max_tvr={MAX_TVR}%\n",
        encoding="utf-8",
    )
    tried_days = []
    for decay_days in DECAY_RETRY_DAYS:
        if decay_days in tried_days:
            continue
        tried_days.append(decay_days)
        try:
            decay_job = make_decay_job(job, decay_days)
        except Exception as exc:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(f"decay{decay_days} setup_failed: {exc}\n")
            continue

        _it, decay_name, decay_xml_path, decay_pnl_file, decay_log_path = decay_job
        rc = run_gsim(decay_xml_path, decay_log_path, timeout)
        if rc != 0:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(f"decay{decay_days} run_failed: rc={rc} log={decay_log_path}\n")
            continue
        try:
            decay_metrics = parse_full_period(simsummary(decay_pnl_file))
        except Exception as exc:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(f"decay{decay_days} simsummary_failed: {exc}\n")
            continue
        with retry_note.open("a", encoding="utf-8") as f:
            f.write(
                f"decay{decay_days}: {decay_name} Sharpe={decay_metrics.get('sharpe')} "
                f"ret={decay_metrics.get('ret_pct')}% tvr={decay_metrics.get('tvr_pct')}%\n"
            )
        if passes_return_gate(decay_metrics) and passes_tvr_gate(decay_metrics):
            return decay_job, decay_metrics
    for first_decay_days, second_decay_days in DOUBLE_DECAY_RETRY_PAIRS:
        try:
            decay_job = make_double_decay_job(job, first_decay_days, second_decay_days)
        except Exception as exc:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(f"decay{first_decay_days}+decay{second_decay_days} setup_failed: {exc}\n")
            continue

        _it, decay_name, decay_xml_path, decay_pnl_file, decay_log_path = decay_job
        rc = run_gsim(decay_xml_path, decay_log_path, timeout)
        if rc != 0:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(
                    f"decay{first_decay_days}+decay{second_decay_days} "
                    f"run_failed: rc={rc} log={decay_log_path}\n"
                )
            continue
        try:
            decay_metrics = parse_full_period(simsummary(decay_pnl_file))
        except Exception as exc:
            with retry_note.open("a", encoding="utf-8") as f:
                f.write(f"decay{first_decay_days}+decay{second_decay_days} simsummary_failed: {exc}\n")
            continue
        with retry_note.open("a", encoding="utf-8") as f:
            f.write(
                f"decay{first_decay_days}+decay{second_decay_days}: {decay_name} "
                f"Sharpe={decay_metrics.get('sharpe')} ret={decay_metrics.get('ret_pct')}% "
                f"tvr={decay_metrics.get('tvr_pct')}%\n"
            )
        if passes_return_gate(decay_metrics) and passes_tvr_gate(decay_metrics):
            return decay_job, decay_metrics
    return None


def maybe_run_high_corr_rescue(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    corr_result: dict,
    timeout: int,
) -> tuple[tuple[int, str, Path, Path, Path], dict, dict | None] | None:
    max_corr = corr_result.get("max_corr") or 0.0
    if max_corr < MAX_CORR or max_corr >= HIGH_CORR_WIDE_RESCUE_MAX:
        return None
    inc_stat("high_corr_rescues")
    _iteration, factor_name, _xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    rescue_note = iter_dir / "high_corr_rescue.txt"
    top = sorted(corr_result["corr_map"].items(), key=lambda item: -item[1])[:5]
    top_text = "\n".join(f"- {name}: {corr:.4f}" for name, corr in top)
    wide = max_corr >= HIGH_CORR_RESCUE_MAX
    suffix = "DecorrWide" if wide else "Decorr"
    severity = (
        "Correlation is very high. This needs a structural rescue, not a small parameter tweak. "
        "Keep the economic family only loosely: replace the main primitive or conditioning regime, change the horizon, and use a simple decorrelating liquidity/volatility mask."
        if wide
        else "The return gates pass, but correlation is slightly too high."
    )
    rescue_context = f"""{severity}
Current max_corr={max_corr:.4f}; gate is max_corr < {MAX_CORR}; rescue is allowed up to {HIGH_CORR_WIDE_RESCUE_MAX}.
Top correlations:
{top_text}

Goal: keep Sharpe/ret above gates, keep tvr_pct < {MAX_TVR}, and reduce overlap with the listed factors. Make a meaningful but still related modification: change the conditioning window, replace one noisy term, or add a simple decorrelating liquidity/volatility mask. Do not adjust term weights or add calibrated-looking weighted gates. Do not merely rename the factor."""
    rescue_note.write_text(rescue_context + "\n", encoding="utf-8")
    try:
        rescue_job = make_rescue_job(job, metrics, suffix=suffix, rescue_context=rescue_context)
    except Exception as exc:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"setup_failed: {exc}\n")
        return None
    _it, rescue_name, rescue_xml_path, rescue_pnl_file, rescue_log_path = rescue_job
    rc = run_gsim(rescue_xml_path, rescue_log_path, timeout)
    if rc != 0:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"run_failed: rc={rc} log={rescue_log_path}\n")
        return None
    try:
        rescue_metrics = parse_full_period(simsummary(rescue_pnl_file))
    except Exception as exc:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"simsummary_failed: {exc}\n")
        return None
    if not passes_return_gate(rescue_metrics):
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(
                f"return_gate_failed: {rescue_name} Sharpe={rescue_metrics.get('sharpe')} "
                f"ret={rescue_metrics.get('ret_pct')}% tvr={rescue_metrics.get('tvr_pct')}%\n"
            )
        return None
    if not passes_tvr_gate(rescue_metrics):
        decay_retry = maybe_run_decay_retry(rescue_job, rescue_metrics, timeout)
        if decay_retry is None:
            with rescue_note.open("a", encoding="utf-8") as f:
                f.write(
                    f"tvr_gate_failed: {rescue_name} Sharpe={rescue_metrics.get('sharpe')} "
                    f"ret={rescue_metrics.get('ret_pct')}% tvr={rescue_metrics.get('tvr_pct')}%\n"
                )
            return None
        rescue_job, rescue_metrics = decay_retry
        _it, rescue_name, _xml, rescue_pnl_file, _log = rescue_job
    new_corr = run_bcorr(rescue_pnl_file)
    with rescue_note.open("a", encoding="utf-8") as f:
        f.write(
            f"rescued: {rescue_name} Sharpe={rescue_metrics.get('sharpe')} "
            f"ret={rescue_metrics.get('ret_pct')}% tvr={rescue_metrics.get('tvr_pct')}% "
            f"max_corr={(new_corr or {}).get('max_corr', 0.0)}\n"
        )
    return rescue_job, rescue_metrics, new_corr


def evaluate_and_install(job: tuple[int, str, Path, Path, Path], metrics: dict, timeout: int = 1200) -> dict:
    iteration, factor_name, _xml_path, pnl_file, _log_path = job
    status = {
        "accepted": False,
        "reason": "",
        "max_corr": None,
        "corr_result": None,
    }
    if not passes_return_gate(metrics):
        status["reason"] = (
            f"return_gate_failed Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}% "
            f"thresholds Sharpe>={MIN_SHARPE} ret>={MIN_RET}%"
        )
        inc_stat("return_gate_failed")
        return status

    if not passes_tvr_gate(metrics):
        decay_retry = maybe_run_decay_retry(job, metrics, timeout)
        if decay_retry is None:
            status["reason"] = (
                f"tvr_gate_failed Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}% "
                f"tvr={metrics.get('tvr_pct')}% threshold tvr<{MAX_TVR}%"
            )
            inc_stat("tvr_gate_failed")
            return status
        job, metrics = decay_retry
        iteration, factor_name, _xml_path, pnl_file, _log_path = job

    corr_result = run_bcorr(pnl_file)
    status["corr_result"] = corr_result
    max_corr = corr_result["max_corr"] if corr_result else 0.0
    status["max_corr"] = max_corr
    if max_corr >= MAX_CORR:
        if corr_result and max_corr < HIGH_CORR_WIDE_RESCUE_MAX:
            corr_rescue = maybe_run_high_corr_rescue(job, metrics, corr_result, timeout)
            if corr_rescue is not None:
                rescue_job, rescue_metrics, rescue_corr = corr_rescue
                rescue_max_corr = rescue_corr["max_corr"] if rescue_corr else 0.0
                if rescue_max_corr < MAX_CORR:
                    install_factor(rescue_job, rescue_metrics, rescue_corr)
                    status["accepted"] = True
                    status["reason"] = "accepted_after_corr_rescue"
                    status["corr_result"] = rescue_corr
                    status["max_corr"] = rescue_max_corr
                    return status
                job, metrics, corr_result = rescue_job, rescue_metrics, rescue_corr
                iteration, factor_name, _xml_path, pnl_file, _log_path = job
                max_corr = rescue_max_corr
                status["corr_result"] = corr_result
                status["max_corr"] = max_corr
        top = sorted(corr_result["corr_map"].items(), key=lambda item: -item[1])[:3] if corr_result else []
        status["reason"] = "corr_gate_failed " + " ".join(f"{name}:{corr:.3f}" for name, corr in top)
        inc_stat("corr_gate_failed")
        (pnl_file.parent.parent / "corr_reject.txt").write_text(
            f"{factor_name} rejected: max_corr={max_corr:.4f} >= {MAX_CORR}\n"
            + (corr_result["raw"] if corr_result else ""),
            encoding="utf-8",
        )
        return status

    install_factor(job, metrics, corr_result)
    inc_stat("accepted")
    status["accepted"] = True
    status["reason"] = "accepted_and_installed"
    return status


def tail_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def build_backtest_feedback_prompt(
    iteration: int,
    factor_name: str,
    metrics: dict | None,
    err: str | None,
    source: str,
    gsim_tail: str,
) -> str:
    if metrics:
        result_block = "\n".join(f"- {key}: {value}" for key, value in metrics.items())
    else:
        result_block = f"- error: {err or 'unknown error'}"
    return f"""You are the feedback agent for an automated Chinese A-share intraday factor miner.

Review one completed factor after gsim backtest. Write concise Chinese feedback for future factor-generation agents.
Focus on actionable lessons, not generic encouragement.
If Sharpe and return are both materially negative, do not call the direction simply wrong.
Treat it as a potentially useful inverse signal and explicitly discuss whether the next step should test negation.

Factor:
- Iteration: {iteration}
- Name: {factor_name}

Backtest result:
{result_block}

Recent gsim log tail:
{gsim_tail or "No gsim log tail available."}

Factor source:
```python
{source}
```

Return exactly these sections:
## Verdict
One sentence: accept / near miss / weak / runtime failure / parse failure.

## Diagnosis
2-5 bullets explaining why the factor likely worked or failed.

## Next-Agent Feedback
3-6 concrete instructions for the next three-agent discussion and codegen. Mention what to avoid repeating.
"""


def write_backtest_feedback(
    run_dir: Path,
    iteration: int,
    factor_name: str,
    iter_dir: Path,
    metrics: dict | None,
    err: str | None,
) -> None:
    source = tail_text(iter_dir / f"{factor_name}.py", max_chars=20000)
    gsim_tail = tail_text(iter_dir / "gsim.log", max_chars=12000)
    prompt = build_backtest_feedback_prompt(iteration, factor_name, metrics, err, source, gsim_tail)
    prompt_path = iter_dir / "agent_feedback_prompt.md"
    response_path = iter_dir / "agent_feedback.md"
    log_path = iter_dir / "agent_feedback.log"
    prompt_path.write_text(prompt, encoding="utf-8")
    try:
        feedback = run_codex_agent(prompt, response_path, log_path, model=FEEDBACK_MODEL).strip()
    except Exception as exc:
        feedback = f"## Verdict\nfeedback agent failed\n\n## Diagnosis\n- {exc}\n\n## Next-Agent Feedback\n- Review logs manually before reusing this idea."
        response_path.write_text(feedback + "\n", encoding="utf-8")

    memory_path = run_dir / "agent_feedback_memory.md"
    with memory_path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n# Iter {iteration}: {factor_name}\n")
        if metrics:
            f.write(
                f"metrics: Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}% "
                f"tvr={metrics.get('tvr_pct')}% dd={metrics.get('dd_pct')}% fitness={metrics.get('fitness')}\n\n"
            )
        else:
            f.write(f"error: {err or 'unknown error'}\n\n")
        f.write(feedback + "\n")


def submit_backtest_feedback(
    run_dir: Path,
    iteration: int,
    factor_name: str,
    iter_dir: Path,
    metrics: dict | None,
    err: str | None,
) -> None:
    if ASYNC_FEEDBACK and _FEEDBACK_POOL is not None:
        _FEEDBACK_FUTURES.append(
            _FEEDBACK_POOL.submit(write_backtest_feedback, run_dir, iteration, factor_name, iter_dir, metrics, err)
        )
        log(f"[iter {iteration}] feedback submitted")
        return
    write_backtest_feedback(run_dir, iteration, factor_name, iter_dir, metrics, err)
    log(f"[iter {iteration}] feedback written")


def collect_feedback(block: bool = False) -> None:
    global _LAST_FEEDBACK_PENDING
    pending: list[Future] = []
    for future in list(_FEEDBACK_FUTURES):
        if block or future.done():
            future.result()
            _FEEDBACK_FUTURES.remove(future)
        else:
            pending.append(future)
    if pending and len(pending) != _LAST_FEEDBACK_PENDING:
        log(f"feedback pending={len(pending)}")
    _LAST_FEEDBACK_PENDING = len(pending)


def build_rescue_discussion_prompt(
    role: str,
    factor_name: str,
    rescue_name: str,
    metrics: dict,
    source: str,
    prior_discussion: str,
    rescue_context: str = "",
) -> str:
    base = f"""You are one of three agents optimizing a near-miss Chinese A-share intraday gsim factor.

The original factor is close to the acceptance threshold. Write concise Chinese notes only. Do not write Python code.

Original factor: {factor_name}
Rescue factor name to be generated later: {rescue_name}
Metrics:
- Sharpe: {metrics.get('sharpe')}
- ret_pct: {metrics.get('ret_pct')}
- tvr_pct: {metrics.get('tvr_pct')}
- dd_pct: {metrics.get('dd_pct')}

Acceptance gates:
- Sharpe >= {MIN_SHARPE}
- ret_pct >= {MIN_RET}
- tvr_pct < {MAX_TVR}
- max_corr < {MAX_CORR}

Rescue context:
{rescue_context or "This is a near-miss return rescue. Improve the factor without overfitting."}

Original source:
```python
{source}
```

Prior agent notes:
{prior_discussion or "No prior notes yet."}
"""
    if role == "optimizer":
        return base + """
Role: Agent 1 / optimizer.
Identify a moderate rescue plan most likely to lift Sharpe above the threshold without destroying return. You may propose 2-3 coordinated changes, such as adding a simple quantile/boolean gate, tightening winsorization, adjusting normalization, or removing/replacing one noisy term. Do not reweight terms with fitted-looking decimals or add nested weighted gates.
Do not replace the factor with an unrelated idea; preserve the core economic mechanism.
Return:
1. Near-miss diagnosis
2. Proposed 2-3 changes
3. Why the combined changes should improve Sharpe
"""
    if role == "risk_reviewer":
        return base + """
Role: Agent 2 / risk reviewer.
Critique Agent 1's moderate rescue proposal for overfitting, turnover blow-up, unavailable data, shape/NaN bugs, and correlation risk against existing similar factors. Keep useful changes, but remove changes that are too large or unrelated.
Return:
1. Keep/change decision
2. Implementation constraints
3. What must not be changed
"""
    return base + """
Role: Agent 3 / final moderate-rescue brief.
Synthesize a final rescue brief. The codegen agent must output a complete Python file with the rescue class name and may change 2-3 coherent details.
Return:
1. Final 2-3 rescue changes
2. Exact behavior to preserve
3. Guardrails for codegen
"""


def run_rescue_discussion(
    factor_name: str,
    rescue_name: str,
    metrics: dict,
    source: str,
    iter_dir: Path,
    rescue_context: str = "",
) -> str:
    roles = ["optimizer", "risk_reviewer", "synthesizer"]
    notes: list[tuple[str, str]] = []
    for idx, role in enumerate(roles, start=1):
        prior = "\n\n".join(f"## Agent {i} ({name})\n{text}" for i, (name, text) in enumerate(notes, start=1))
        prompt = build_rescue_discussion_prompt(
            role, factor_name, rescue_name, metrics, source, prior, rescue_context
        )
        prompt_path = iter_dir / f"rescue_agent_{idx}_{role}_prompt.md"
        response_path = iter_dir / f"rescue_agent_{idx}_{role}_response.md"
        log_path = iter_dir / f"rescue_agent_{idx}_{role}.log"
        prompt_path.write_text(prompt, encoding="utf-8")
        response = run_codex_agent(prompt, response_path, log_path, model=DISCUSSION_MODEL)
        notes.append((role, response.strip()))

    discussion = "\n\n".join(
        f"## Agent {idx} ({role})\n{text}" for idx, (role, text) in enumerate(notes, start=1)
    )
    (iter_dir / "rescue_agent_discussion.md").write_text(discussion + "\n", encoding="utf-8")
    return discussion


def build_rescue_codegen_prompt(
    factor_name: str,
    rescue_name: str,
    metrics: dict,
    source: str,
    discussion: str,
    rescue_context: str = "",
) -> str:
    return f"""You are modifying a near-miss Chinese A-share intraday gsim AlphaBase factor.

Return ONLY a complete Python source file. Do not use markdown fences. Do not explain.

Hard contract:
- Define exactly one class named {rescue_name}(AlphaBase).
- Start with:
  from gsim import DataRegistry as dr
  from gsim import AlphaBase
  import numpy as np
- Use only local gsim data through dr.getData(...).data.
- Do not import network, pandas, sklearn, scipy, os, pathlib, subprocess, or external services.
- Safe known datasets: 'volume', 'open', 'close', 'high', 'low', 'amount',
  'Interval5m.open', 'Interval5m.close', 'Interval5m.amo',
  'revenue_forecast_annual.revenue', 'revenue_forecast_annual.forecast_year',
  'revenue_forecast_quarter.revenue', 'revenue_forecast_quarter.forecast_quarter',
  'financial_summary_fore_annual.IS01', 'financial_summary_fore_annual.IS02',
  'financial_summary_fore_annual.IS23', 'financial_summary_fore_annual.ID05',
  'financial_summary_fore_annual.IS20', 'financial_summary_fore_annual.ID04',
  'financial_summary_fore_annual.IS36', 'financial_summary_fore_annual.ID06',
  'financial_summary_fore_annual.IS42', 'financial_summary_fore_annual.forecast_year',
  'financial_summary_fore_quarter.IS01_q', 'financial_summary_fore_quarter.IS02_q',
  'financial_summary_fore_quarter.IS36_q', 'financial_summary_fore_quarter.ID06_q',
  'financial_summary_fore_quarter.IS42_q', 'financial_summary_fore_quarter.forecast_quarter'.
- Do not use 'Interval5m.volume', 'Interval5m.high', or 'Interval5m.low'.
- Keep the data family clean. Do not mix forecast datasets with Interval5m data unless the rescue context explicitly requires both.
- Keep the rescue simple: one core primitive and at most one guard/mask change.
- Keep delay=0 compatibility and do not look into future days.
- Must assign self.alpha[valid_idx].

Original factor: {factor_name}
Original metrics:
- Sharpe: {metrics.get('sharpe')}
- ret_pct: {metrics.get('ret_pct')}
- tvr_pct: {metrics.get('tvr_pct')}
- dd_pct: {metrics.get('dd_pct')}

Rescue context:
{rescue_context or "This is a near-miss return rescue. Improve the factor without overfitting."}

Three-agent rescue discussion:
{discussion}

Original source:
```python
{source}
```

Apply the final moderate-rescue brief from the discussion. You may change 2-3 coherent details, but preserve the original data contract and core economic mechanism. Do not create a totally new factor.
"""


def make_rescue_job(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    suffix: str = "Rescue",
    rescue_context: str = "",
) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    rescue_name = f"{factor_name}{suffix}"
    source_path = iter_dir / f"{factor_name}.py"
    rescue_source_path = iter_dir / f"{rescue_name}.py"
    suffix_key = suffix.lower()
    rescue_xml_path = iter_dir / f"Config.tinyclaw.{suffix_key}.xml"
    rescue_log_path = iter_dir / f"gsim.{suffix_key}.log"
    rescue_pnl_dir = iter_dir / f"pnl_{suffix_key}"
    rescue_pnl_dir.mkdir(parents=True, exist_ok=True)

    source = source_path.read_text(encoding="utf-8")
    discussion = run_rescue_discussion(factor_name, rescue_name, metrics, source, iter_dir, rescue_context)
    prompt = build_rescue_codegen_prompt(factor_name, rescue_name, metrics, source, discussion, rescue_context)
    (iter_dir / f"rescue_{suffix_key}_codegen_prompt.md").write_text(prompt, encoding="utf-8")
    response_path = iter_dir / f"rescue_{suffix_key}_agent_response.md"
    log_path_codegen = iter_dir / f"rescue_{suffix_key}_agent_codegen.log"
    response = run_codex_agent(prompt, response_path, log_path_codegen)
    rescue_source = extract_python_source(response)
    validate_agent_source(rescue_source, rescue_name, response_path)
    rescue_source_path.write_text(rescue_source, encoding="utf-8")

    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = xml_text.replace(str(source_path), str(rescue_source_path))
    xml_text = xml_text.replace(f'{factor_name}mod', f'{rescue_name}mod')
    xml_text = xml_text.replace(f'id="{factor_name}"', f'id="{rescue_name}"')
    xml_text = xml_text.replace(str(pnl_file.parent), str(rescue_pnl_dir))
    rescue_xml_path.write_text(xml_text, encoding="utf-8")
    return iteration, rescue_name, rescue_xml_path, rescue_pnl_dir / rescue_name, rescue_log_path


def maybe_run_near_miss_rescue(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    timeout: int,
) -> tuple[tuple[int, str, Path, Path, Path], dict] | None:
    if not should_near_miss_rescue(metrics):
        return None
    inc_stat("near_miss_rescues")
    iteration, factor_name, _xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    rescue_note = iter_dir / "near_miss_rescue.txt"
    rescue_note.write_text(
        f"triggered: {factor_name} Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}%\n",
        encoding="utf-8",
    )
    try:
        rescue_job = make_rescue_job(job, metrics)
    except Exception as exc:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"setup_failed: {exc}\n")
        return None

    _iteration, rescue_name, rescue_xml_path, rescue_pnl_file, rescue_log_path = rescue_job
    rc = run_gsim(rescue_xml_path, rescue_log_path, timeout)
    if rc != 0:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"run_failed: rc={rc} log={rescue_log_path}\n")
        return None
    try:
        rescue_metrics = parse_full_period(simsummary(rescue_pnl_file))
    except Exception as exc:
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(f"simsummary_failed: {exc}\n")
        return None

    with rescue_note.open("a", encoding="utf-8") as f:
        f.write(
            f"rescued: {rescue_name} Sharpe={rescue_metrics.get('sharpe')} "
            f"ret={rescue_metrics.get('ret_pct')}% tvr={rescue_metrics.get('tvr_pct')}%\n"
        )
    return rescue_job, rescue_metrics


def make_power_rescue_job(
    job: tuple[int, str, Path, Path, Path],
    exp: float,
) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    iter_dir = log_path.parent
    suffix = f"power{str(exp).replace('.', 'p')}"
    power_name = f"{factor_name}Power{str(exp).replace('.', 'p')}"
    power_xml_path = iter_dir / f"Config.tinyclaw.{suffix}.xml"
    power_log_path = iter_dir / f"gsim.{suffix}.log"
    power_pnl_dir = iter_dir / f"pnl_{suffix}"
    power_pnl_dir.mkdir(parents=True, exist_ok=True)

    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = xml_text.replace(f'id="{factor_name}"', f'id="{power_name}"')
    xml_text = re.sub(r'(<Operation module="AlphaOpPower" exp=")[0-9.]+(")', rf"\g<1>{exp:g}\2", xml_text, count=1)
    xml_text = xml_text.replace(str(pnl_file.parent), str(power_pnl_dir))
    power_xml_path.write_text(xml_text, encoding="utf-8")
    return iteration, power_name, power_xml_path, power_pnl_dir / power_name, power_log_path


def maybe_run_power_rescue(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    timeout: int,
) -> tuple[tuple[int, str, Path, Path, Path], dict] | None:
    if not should_near_miss_rescue(metrics):
        return None
    inc_stat("power_rescues")
    iteration, factor_name, _xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    rescue_note = iter_dir / "power_rescue.txt"
    rescue_note.write_text(
        f"triggered: {factor_name} Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}%\n",
        encoding="utf-8",
    )
    best: tuple[tuple[int, str, Path, Path, Path], dict] | None = None
    for exp in POWER_RESCUE_EXPS:
        if exp <= 1.0 or exp > 2.0:
            continue
        try:
            power_job = make_power_rescue_job(job, exp)
        except Exception as exc:
            with rescue_note.open("a", encoding="utf-8") as f:
                f.write(f"power{exp:g} setup_failed: {exc}\n")
            continue
        _iteration, power_name, power_xml_path, power_pnl_file, power_log_path = power_job
        rc = run_gsim(power_xml_path, power_log_path, timeout)
        if rc != 0:
            with rescue_note.open("a", encoding="utf-8") as f:
                f.write(f"power{exp:g} run_failed: rc={rc} log={power_log_path}\n")
            continue
        try:
            power_metrics = parse_full_period(simsummary(power_pnl_file))
        except Exception as exc:
            with rescue_note.open("a", encoding="utf-8") as f:
                f.write(f"power{exp:g} simsummary_failed: {exc}\n")
            continue
        with rescue_note.open("a", encoding="utf-8") as f:
            f.write(
                f"power{exp:g}: {power_name} Sharpe={power_metrics.get('sharpe')} "
                f"ret={power_metrics.get('ret_pct')}% tvr={power_metrics.get('tvr_pct')}%\n"
            )
        if passes_return_gate(power_metrics) and passes_tvr_gate(power_metrics):
            return power_job, power_metrics
        if best is None or (power_metrics.get("sharpe") or -999) > (best[1].get("sharpe") or -999):
            best = (power_job, power_metrics)
    return None


def run_one(job: tuple[int, str, Path, Path, Path], timeout: int) -> tuple[int, str, int, dict | None, str | None, dict | None]:
    iteration, factor_name, xml_path, pnl_file, log_path = job
    if PRESCREEN_ENABLED:
        prescreen_job = make_prescreen_job(job)
        _it, _name, prescreen_xml, prescreen_pnl_file, prescreen_log = prescreen_job
        rc_pre = run_gsim(prescreen_xml, prescreen_log, min(timeout, PRESCREEN_TIMEOUT))
        if rc_pre != 0:
            inc_stat("backtest_failed")
            return iteration, factor_name, rc_pre, None, f"prescreen gsim failed rc={rc_pre} log={prescreen_log}", None
        try:
            prescreen_metrics = parse_full_period(simsummary(prescreen_pnl_file))
        except Exception as exc:
            inc_stat("simsummary_failed")
            return iteration, factor_name, rc_pre, None, f"prescreen simsummary failed: {exc}", None
        if not passes_prescreen(prescreen_metrics):
            inc_stat("return_gate_failed")
            status = {
                "accepted": False,
                "reason": (
                    f"prescreen_failed Sharpe={prescreen_metrics.get('sharpe')} "
                    f"ret={prescreen_metrics.get('ret_pct')}% "
                    f"thresholds Sharpe>={PRESCREEN_MIN_SHARPE} ret>={PRESCREEN_MIN_RET}%"
                ),
                "max_corr": None,
                "corr_result": None,
            }
            return iteration, factor_name, 0, prescreen_metrics, None, status

    rc = run_gsim(xml_path, log_path, timeout)
    if rc != 0:
        inc_stat("backtest_failed")
        return iteration, factor_name, rc, None, f"gsim failed rc={rc} log={log_path}", None
    try:
        metrics = parse_full_period(simsummary(pnl_file))
    except Exception as exc:
        inc_stat("simsummary_failed")
        return iteration, factor_name, rc, None, f"simsummary failed: {exc}", None

    final_job = job
    final_metrics = metrics
    retry = maybe_run_negate_retry(job, metrics, timeout)
    if retry is not None:
        final_job, final_metrics = retry
    if not passes_return_gate(final_metrics):
        rescue = maybe_run_power_rescue(final_job, final_metrics, timeout)
        if rescue is None:
            rescue = maybe_run_near_miss_rescue(final_job, final_metrics, timeout)
        if rescue is not None:
            final_job, final_metrics = rescue

    install_status = evaluate_and_install(final_job, final_metrics, timeout)
    final_iteration, final_factor_name, _final_xml, _final_pnl, _final_log = final_job
    return final_iteration, final_factor_name, rc, final_metrics, None, install_status


def should_negate_retry(metrics: dict) -> bool:
    sharpe = metrics.get("sharpe")
    ret = metrics.get("ret_pct")
    if sharpe is None or ret is None:
        return False
    return sharpe <= -NEGATE_RETRY_MIN_ABS_SHARPE and ret <= -NEGATE_RETRY_MIN_ABS_RET


def make_negated_job(job: tuple[int, str, Path, Path, Path]) -> tuple[int, str, Path, Path, Path]:
    iteration, factor_name, xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    negated_name = f"{factor_name}Neg"
    source_path = iter_dir / f"{factor_name}.py"
    neg_source_path = iter_dir / f"{negated_name}.py"
    neg_xml_path = iter_dir / "Config.tinyclaw.neg.xml"
    neg_log_path = iter_dir / "gsim.neg.log"
    neg_pnl_dir = iter_dir / "pnl_neg"
    neg_pnl_dir.mkdir(parents=True, exist_ok=True)

    source = source_path.read_text(encoding="utf-8")
    neg_source = negate_alpha_source(source, factor_name, negated_name)
    response_path = iter_dir / f"{negated_name}.py"
    validate_agent_source(neg_source, negated_name, response_path)
    neg_source_path.write_text(neg_source, encoding="utf-8")

    xml_text = xml_path.read_text(encoding="utf-8")
    xml_text = xml_text.replace(str(source_path), str(neg_source_path))
    xml_text = xml_text.replace(f'{factor_name}mod', f'{negated_name}mod')
    xml_text = xml_text.replace(f'id="{factor_name}"', f'id="{negated_name}"')
    xml_text = xml_text.replace(str(iter_dir / "pnl"), str(neg_pnl_dir))
    neg_xml_path.write_text(xml_text, encoding="utf-8")
    return iteration, negated_name, neg_xml_path, neg_pnl_dir / negated_name, neg_log_path


def maybe_run_negate_retry(
    job: tuple[int, str, Path, Path, Path],
    metrics: dict,
    timeout: int,
) -> tuple[tuple[int, str, Path, Path, Path], dict] | None:
    if not should_negate_retry(metrics):
        return None
    inc_stat("negate_retries")
    iteration, factor_name, _xml_path, _pnl_file, log_path = job
    iter_dir = log_path.parent
    retry_note = iter_dir / "negate_retry.txt"
    retry_note.write_text(
        f"triggered: {factor_name} Sharpe={metrics.get('sharpe')} ret={metrics.get('ret_pct')}%\n",
        encoding="utf-8",
    )
    try:
        neg_job = make_negated_job(job)
    except Exception as exc:
        with retry_note.open("a", encoding="utf-8") as f:
            f.write(f"setup_failed: {exc}\n")
        return None

    _iteration, negated_name, neg_xml_path, neg_pnl_file, neg_log_path = neg_job
    rc = run_gsim(neg_xml_path, neg_log_path, timeout)
    if rc != 0:
        with retry_note.open("a", encoding="utf-8") as f:
            f.write(f"run_failed: rc={rc} log={neg_log_path}\n")
        return None
    try:
        neg_metrics = parse_full_period(simsummary(neg_pnl_file))
    except Exception as exc:
        with retry_note.open("a", encoding="utf-8") as f:
            f.write(f"simsummary_failed: {exc}\n")
        return None

    with retry_note.open("a", encoding="utf-8") as f:
        f.write(
            f"negated: {negated_name} Sharpe={neg_metrics.get('sharpe')} "
            f"ret={neg_metrics.get('ret_pct')}% tvr={neg_metrics.get('tvr_pct')}%\n"
        )
    return neg_job, neg_metrics


def collect_finished(run_dir: Path,
                     futures: dict[Future, tuple[int, str, Path, Path, Path]],
                     results: list[tuple[str, dict]], *, block: bool = False) -> None:
    done = []
    if block:
        iterator = as_completed(futures)
    else:
        iterator = (future for future in list(futures) if future.done())
    for future in iterator:
        iteration, factor_name, _rc, metrics, err, install_status = future.result()
        _job_iteration, _job_factor_name, _xml_path, _pnl_file, log_path = futures[future]
        iter_dir = log_path.parent
        done.append(future)
        if err is not None:
            log(f"[iter {iteration}] {err}")
            gsim_match = re.search(r"gsim failed rc=(\d+)", err)
            if gsim_match:
                err_log_match = re.search(r"log=([^ ]+)", err)
                err_log_path = Path(err_log_match.group(1)) if err_log_match else log_path
                append_backtest_failure_diagnostic(
                    run_dir,
                    iteration,
                    factor_name,
                    int(gsim_match.group(1)),
                    err,
                    err_log_path,
                    int(_RUN_STATE.get("args", {}).get("timeout", 0) or 0),
                )
            submit_backtest_feedback(run_dir, iteration, factor_name, iter_dir, metrics, err)
            record_result(run_dir, iteration, factor_name, metrics, err, install_status)
            continue
        assert metrics is not None
        results.append((factor_name, metrics))
        log(
            f"[iter {iteration}] Sharpe={metrics.get('sharpe')} "
            f"ret={metrics.get('ret_pct')}% tvr={metrics.get('tvr_pct')}%"
        )
        if install_status:
            if install_status.get("accepted"):
                log(
                    f"[iter {iteration}] accepted installed max_corr={install_status.get('max_corr')}"
                )
            else:
                log(f"[iter {iteration}] rejected {install_status.get('reason')}")
        submit_backtest_feedback(run_dir, iteration, factor_name, iter_dir, metrics, err)
        record_result(run_dir, iteration, factor_name, metrics, err, install_status)
        collect_feedback(block=False)
        if not block:
            break
    for future in done:
        futures.pop(future, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tinyKaggleClaw local gsim factor miner")
    parser.add_argument("--seed", default="IntraDay")
    parser.add_argument("--iters", type=int, default=40)
    parser.add_argument("--parallel", type=int, default=8)
    parser.add_argument("--start-date", default="20190101")
    parser.add_argument("--end-date", default="20241231")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--factor-prefix", default="AlphaTinyClaw")
    args = parser.parse_args(argv)

    global _RUN_LOG, _FEEDBACK_POOL
    run_dir = make_run_dir(args.seed)
    _RUN_LOG = run_dir / "launch.log"
    _FEEDBACK_POOL = ThreadPoolExecutor(max_workers=2) if ASYNC_FEEDBACK else None
    _RUN_LOG.write_text("", encoding="utf-8")
    init_run_tracking(run_dir, args)
    log(f"run_dir={run_dir}")
    log(f"launch_log={_RUN_LOG}")
    log(f"iters={args.iters} parallel={args.parallel} start={args.start_date} end={args.end_date}")
    log(f"python={sys.executable}")
    log(f"gsim_python={GSIM_PYTHON}")
    log(f"gsim_run={GSIM_RUN}")
    log(
        f"codegen_model={CODEX_MODEL} decay_days={DECAY_DAYS} "
        f"codegen_temperature={CODEGEN_TEMPERATURE or 'default'} "
        f"discussion_model={DISCUSSION_MODEL} feedback_model={FEEDBACK_MODEL} "
        f"discussion_agents={DISCUSSION_AGENTS} discussion_parallel={DISCUSSION_PARALLEL} "
        f"async_feedback={ASYNC_FEEDBACK} codegen_parallel={CODEGEN_PARALLEL} "
        f"forecast_data={FORECAST_DATA_ENABLED} nio_data_path={NIO_DATA_PATH} "
        f"prescreen={PRESCREEN_ENABLED} direction_vectors={len(DIRECTION_PROFILES)} "
        f"direction_axes={','.join(DIRECTION_VECTOR_AXES)} "
        f"one_family_per_run={ONE_FAMILY_PER_RUN}"
    )
    if has_strict_replication_target():
        log(
            "strict_replication=true "
            f"slug={FACTOR_LIBRARY_TARGET_SLUG} "
            f"title={FACTOR_LIBRARY_TARGET_TITLE or FACTOR_LIBRARY_TARGET_SLUG}"
        )
    run_direction_idx: int | None = None
    run_direction: str | None = None
    if ONE_FAMILY_PER_RUN:
        run_direction_idx, run_direction = next_direction()
        write_run_direction_family(run_dir, run_direction_idx, run_direction)
        log(f"run_direction_family={iteration_target_log(run_direction_idx, run_direction)}")

    if args.dry_run:
        generated = 0
        for iteration in range(1, args.iters + 1):
            if ONE_FAMILY_PER_RUN:
                assert run_direction_idx is not None and run_direction is not None
                direction_idx, direction = run_direction_idx, run_direction
            else:
                direction_idx, direction = next_direction()
            log(f"[iter {iteration}] {iteration_target_log(direction_idx, direction)}")
            log(f"[iter {iteration}] codegen=codex model={CODEX_MODEL}")
            try:
                job = write_candidate(run_dir, iteration, direction_idx, direction, args.start_date, args.end_date, args.factor_prefix)
            except Exception as exc:
                log(f"[iter {iteration}] codegen failed: {exc}")
                continue
            generated += 1
            log(f"[iter {iteration}] factor={job[1]}")
        log("dry_run=true; generated code/XML only")
        if generated == 0:
            log("no valid generated candidates; done")
            return 1
        log("done")
        return 0

    results = []
    submitted = 0
    generated = 0
    next_iter = 1
    codegen_workers = max(1, min(CODEGEN_PARALLEL, args.parallel))
    log(
        f"pipeline start: iters={args.iters} parallel={args.parallel} "
        f"codegen_parallel={codegen_workers} mode=decoupled_codegen_backtest"
    )
    with ThreadPoolExecutor(max_workers=args.parallel) as backtest_pool, ThreadPoolExecutor(max_workers=codegen_workers) as codegen_pool:
        backtest_futures: dict[Future, tuple[int, str, Path, Path, Path]] = {}
        codegen_futures: dict[Future, tuple[int, int, str]] = {}

        def submit_codegen(iteration: int) -> None:
            if ONE_FAMILY_PER_RUN:
                assert run_direction_idx is not None and run_direction is not None
                direction_idx, direction = run_direction_idx, run_direction
            else:
                direction_idx, direction = next_direction()
            log(f"[iter {iteration}] {iteration_target_log(direction_idx, direction)}")
            log(f"[iter {iteration}] codegen=codex model={CODEX_MODEL}")
            future = codegen_pool.submit(
                write_candidate,
                run_dir,
                iteration,
                direction_idx,
                direction,
                args.start_date,
                args.end_date,
                args.factor_prefix,
            )
            codegen_futures[future] = (iteration, direction_idx, direction)
            inc_stat("codegen_submitted")

        while next_iter <= args.iters and len(codegen_futures) < codegen_workers:
            submit_codegen(next_iter)
            next_iter += 1

        while codegen_futures or backtest_futures or next_iter <= args.iters:
            for future in list(codegen_futures):
                if not future.done():
                    continue
                iteration, _direction_idx, _direction = codegen_futures.pop(future)
                try:
                    job = future.result()
                except Exception as exc:
                    inc_stat("codegen_failed")
                    log(f"[iter {iteration}] codegen failed: {exc}")
                    record_result(run_dir, iteration, f"iter_{iteration:03d}", None, f"codegen failed: {exc}", None)
                else:
                    generated += 1
                    log(f"[iter {iteration}] factor={job[1]}")
                    backtest_futures[backtest_pool.submit(run_one, job, args.timeout)] = job
                    submitted += 1
                    inc_stat("backtest_submitted")
                    log(f"[iter {iteration}] backtest submitted")
                while next_iter <= args.iters and len(codegen_futures) < codegen_workers:
                    submit_codegen(next_iter)
                    next_iter += 1

            collect_finished(run_dir, backtest_futures, results, block=False)
            collect_feedback(block=False)
            if codegen_futures or backtest_futures:
                try:
                    next(as_completed(list(codegen_futures) + list(backtest_futures), timeout=5))
                except FuturesTimeoutError:
                    write_run_status(run_dir)

        if submitted == 0:
            log("no valid generated candidates; done")
            return 1

        log(f"pipeline generated={generated} submitted={submitted}; waiting for backtests")
        collect_finished(run_dir, backtest_futures, results, block=True)

    if results:
        best = max(results, key=lambda item: item[1].get("sharpe") or -999)
        log(f"best={best[0]} metrics={best[1]}")
    collect_feedback(block=True)
    if _FEEDBACK_POOL is not None:
        _FEEDBACK_POOL.shutdown(wait=True)
        _FEEDBACK_POOL = None
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
