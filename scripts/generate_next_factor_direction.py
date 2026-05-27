#!/usr/bin/env python3
"""Generate a new factor-mining direction for the next round.

Each new round must explore a direction vector that has not appeared in recent
rounds. Installed-factor vectors are still used for scarcity scoring, but recent
run vectors are a hard exclusion by default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cluster_factor_directions import FEATURE_RULES, cluster_rows, factor_sources, load_direction_profiles, summarize_source  # noqa: E402


AXES = [name for name, _tokens in FEATURE_RULES]
REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRE_NEW_DIRECTION_PER_ROUND = os.environ.get(
    "FACTOR_MINER_REQUIRE_NEW_DIRECTION_PER_ROUND",
    "true",
).lower() not in {"0", "false", "no"}
RECENT_DIRECTION_EXCLUDE_LIMIT = int(os.environ.get("FACTOR_MINER_RECENT_DIRECTION_EXCLUDE_LIMIT", "64"))
FORECAST_DATA_ENABLED = os.environ.get("FACTOR_MINER_ENABLE_FORECAST_DATA", "true").lower() not in {
    "0",
    "false",
    "no",
}
FORECAST_AXES = {"forecast_revenue", "forecast_profitability", "forecast_revision"}


def profile_key(profile: dict) -> str:
    return "".join(str(profile["vector"].get(name, 0)) for name, _tokens in FEATURE_RULES)


def key_to_vector(key: str) -> dict[str, int]:
    return dict(zip(AXES, (int(char) for char in key)))


def vector_key(vector: dict[str, int]) -> str:
    return "".join(str(vector.get(axis, 0)) for axis in AXES)


def hamming(left: dict[str, int], right: dict[str, int]) -> int:
    return sum(int(left.get(axis, 0) != right.get(axis, 0)) for axis in AXES)


def active_axes(vector: dict[str, int]) -> list[str]:
    return [axis for axis in AXES if vector.get(axis, 0)]


def recent_run_vectors(limit: int | None = None) -> list[dict[str, int]]:
    vectors: list[dict[str, int]] = []
    limit = RECENT_DIRECTION_EXCLUDE_LIMIT if limit is None else limit
    run_root = REPO_ROOT / "output" / "factor_mining"
    runs = sorted(
        run_root.glob("factor_mining_IntraDay_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_dir in runs[:limit]:
        path = run_dir / "direction_family.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        marker = "- full vector:"
        line = next((item for item in text.splitlines() if item.startswith(marker)), "")
        if not line:
            continue
        values: dict[str, int] = {}
        for part in line.removeprefix(marker).strip().split(","):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            if name in AXES and value.strip() in {"0", "1"}:
                values[name] = int(value.strip())
        if all(axis in values for axis in AXES):
            vectors.append(values)
    return vectors


def candidate_vectors() -> list[dict[str, int]]:
    candidates = []
    for bits in range(1, 1 << len(AXES)):
        values = [(bits >> idx) & 1 for idx in range(len(AXES))]
        active_count = sum(values)
        if 4 <= active_count <= 8:
            vector = dict(zip(AXES, values))
            has_intraday = vector["intraday_price_path"] or vector["intraday_amount_path"]
            has_forecast = vector["forecast_revenue"] or vector["forecast_profitability"] or vector["forecast_revision"]
            if has_forecast and not FORECAST_DATA_ENABLED:
                continue
            if has_forecast and has_intraday:
                continue
            has_liquidity_axis = vector["daily_volume_amount"] or vector["intraday_amount_path"] or vector["liquidity_regime"]
            coherent_price_amount = not vector["price_volume_divergence"] or (
                (vector["daily_price"] or vector["intraday_price_path"]) and has_liquidity_axis
            )
            coherent_forecast = not has_forecast or (
                vector["forecast_revenue"] or vector["forecast_profitability"]
            )
            if (has_intraday or has_forecast) and coherent_price_amount and coherent_forecast:
                candidates.append(vector)
    return candidates


def score_vector(
    vector: dict[str, int],
    installed_vectors: list[dict[str, int]],
    profile_vectors: list[dict[str, int]],
    recent_vectors: list[dict[str, int]],
    axis_counts: Counter[str],
    factor_count: int,
) -> tuple:
    axes = active_axes(vector)
    installed_distance = min((hamming(vector, item) for item in installed_vectors), default=len(AXES))
    profile_distance = min((hamming(vector, item) for item in profile_vectors), default=len(AXES))
    recent_distance = min((hamming(vector, item) for item in recent_vectors), default=len(AXES))
    scarcity = sum(1.0 - (axis_counts[axis] / max(1, factor_count)) for axis in axes)
    undercovered_core = sum(
        vector.get(axis, 0)
        for axis in (
            "price_volume_divergence",
            "time_of_day_seasonality",
            "persistence_decay_control",
            "forecast_revenue",
            "forecast_profitability",
            "forecast_revision",
            "return_momentum",
            "nonlinear_distribution",
        )
    )
    overcrowded_penalty = sum(
        vector.get(axis, 0)
        for axis in (
            "daily_volume_amount",
            "intraday_amount_path",
            "cross_sectional_state",
            "tail_extreme_focus",
        )
    )
    return (
        installed_distance,
        recent_distance,
        profile_distance,
        scarcity,
        undercovered_core,
        -overcrowded_penalty,
        -abs(len(axes) - 7),
        vector_key(vector),
    )


def describe_new_direction(vector: dict[str, int], rare_axes: list[str], avoid_examples: list[str]) -> dict:
    axes = active_axes(vector)
    has = vector.get
    if has("forecast_revenue") and has("forecast_profitability") and has("forecast_revision") and not (
        has("intraday_price_path") or has("intraday_amount_path")
    ):
        title = "全新方向: AI收入盈利预测一致性变化簇"
        cluster = "ai_revenue_profitability_forecast_consistency"
        mechanism = (
            "compare AI revenue forecast change with forecast profitability change, then test whether the market "
            "has already priced that consistency or conflict through daily price/liquidity behavior"
        )
        data = "revenue_forecast_* + income_statement_fore_* or financial_summary_fore_* + daily OHLCV/amount"
    elif has("forecast_revenue") and has("forecast_revision"):
        title = "全新方向: AI收入预测修正与日频确认簇"
        cluster = "ai_revenue_forecast_revision_daily_confirm"
        mechanism = (
            "use AI revenue forecast revision, slope, or near-vs-far contrast as the primary primitive, "
            "then use one simple daily OHLCV/amount primitive to check whether the market has already priced it"
        )
        data = "revenue_forecast_annual/quarter + one complementary forecast family + daily OHLCV/amount; do not use Interval5m"
    elif has("forecast_profitability") and has("forecast_revision"):
        title = "全新方向: AI盈利质量变化与价格反应错配簇"
        cluster = "ai_profitability_forecast_response_mismatch"
        mechanism = (
            "treat forecast profitability level/change as a first-class signal and measure underreaction or "
            "overreaction in daily or intraday price paths"
        )
        data = "income_statement_fore_*, financial_summary_fore_*, cash_flow_statement_fore_annual, balance_sheet_fore_annual, or finance_ratio_fore_annual + daily OHLCV/amount; do not use Interval5m"
    elif has("forecast_revenue") or has("forecast_profitability"):
        title = "全新方向: AI预测水平与日频定价偏差簇"
        cluster = "ai_forecast_level_daily_mispricing"
        mechanism = (
            "use one AI forecast level primitive, such as revenue or profitability expectation, and compare it "
            "with one simple daily price or liquidity state"
        )
        data = "one forecast dataset + daily OHLCV/amount; do not use Interval5m"
    elif has("daily_volume_amount") and has("liquidity_regime") and has("event_time_segmentation") and not (
        has("intraday_price_path") or has("intraday_amount_path")
    ):
        title = "全新方向: 日频资金流与事件状态错配簇"
        cluster = "daily_moneyflow_event_state_mismatch"
        mechanism = (
            "use one table-qualified cc_all field from money-flow, auction, or high-frequency daily derivative data "
            "as the event/liquidity state, then compare it with one daily price response variable"
        )
        data = "AShareMoneyFlow or hf_daily_auction_table/hf_daily_der_table_* + daily OHLCV/amount; avoid raw 5m tensors"
    elif has("volatility_regime") and has("return_momentum") and has("daily_price") and not (
        has("intraday_price_path") or has("intraday_amount_path")
    ):
        title = "全新方向: 预计算风险收益状态下的价格反应簇"
        cluster = "precomputed_return_risk_daily_response"
        mechanism = (
            "use one precomputed equ_factor_return/trend/volume/quality field as the stock state, then test whether "
            "daily price action shows continuation or overreaction"
        )
        data = "equ_factor_* + daily OHLCV/amount; avoid Interval5m unless explicitly required"
    elif has("price_volume_divergence") and has("time_of_day_seasonality") and has("persistence_decay_control"):
        title = "全新方向: 分时价格-成交错位的持续性确认簇"
        cluster = "timed_price_amount_mismatch_persistence"
        mechanism = (
            "measure whether amount migrates to price zones that disagree with the intraday repair path, "
            "then require the mismatch to persist across event-time bars rather than appear as a one-bar spike"
        )
        data = "Interval5m.close/open/amo + daily OHLC location + own recent 5m amount baseline"
    elif has("nonlinear_distribution") and has("return_momentum"):
        title = "全新方向: 非线性路径形态下的迟滞动量簇"
        cluster = "nonlinear_path_lagged_momentum"
        mechanism = (
            "use path-shape entropy or signed rank dispersion to find stocks whose return path lags an otherwise "
            "coherent intraday move, without relying on broad amount autocorrelation"
        )
        data = "Interval5m.close/open + optional Interval5m.amo share baseline"
    else:
        title = "全新方向: 稀缺特征轴组合探索簇"
        cluster = "novel_sparse_axis_combo"
        mechanism = (
            "combine under-covered feature axes into one simple hypothesis, using one event-time selection and one "
            "interpretable confirmation variable"
        )
        data = "one clean data family: either daily OHLCV/amount, forecast datasets, or Interval5m.open/close/amo"
    return {
        "idx": 0,
        "title": title,
        "cluster": cluster,
        "vector": vector,
        "active_axes": axes,
        "data": data,
        "mechanism": (
            f"{mechanism}; emphasize rare axes {', '.join(rare_axes) or 'none'}; keep the expression simple and avoid "
            "nested decimal-weight blends"
        ),
        "avoid": (
            "do not use any static direction profile verbatim; avoid close variants of "
            f"{', '.join(dict.fromkeys(avoid_examples)) or 'installed dominant clusters'}"
        ),
    }


def choose_direction(rows: list[dict], clusters: list[dict], profiles: list[dict]) -> dict:
    factor_count = len(rows)
    axis_counts: Counter[str] = Counter()
    for row in rows:
        axis_counts.update(row["active_axes"])
    installed_keys = {cluster["cluster_key"] for cluster in clusters}
    profile_vectors = [profile["vector"] for profile in profiles]
    profile_keys = {profile_key(profile) for profile in profiles}
    installed_vectors = [key_to_vector(key) for key in installed_keys]
    recent_vectors = recent_run_vectors()
    recent_keys = {vector_key(vector) for vector in recent_vectors}
    eligible = [
        vector
        for vector in candidate_vectors()
        if vector_key(vector) not in installed_keys
        and vector_key(vector) not in profile_keys
        and (not REQUIRE_NEW_DIRECTION_PER_ROUND or vector_key(vector) not in recent_keys)
    ]
    if not eligible:
        raise SystemExit(
            "no novel feature-vector direction available after excluding installed, static, "
            f"and recent run vectors (recent_limit={RECENT_DIRECTION_EXCLUDE_LIMIT})"
        )
    chosen_vector = max(
        eligible,
        key=lambda vector: score_vector(vector, installed_vectors, profile_vectors, recent_vectors, axis_counts, factor_count),
    )
    axes = active_axes(chosen_vector)
    rare_axes = sorted(axes, key=lambda axis: (axis_counts[axis], axis))[:5]
    crowded_clusters = clusters[:3]
    avoid_examples = []
    for cluster in crowded_clusters:
        avoid_examples.extend(member["factor"] for member in cluster["members"][:2])
    chosen = describe_new_direction(chosen_vector, rare_axes, avoid_examples)
    nearest_installed = min((hamming(chosen_vector, item) for item in installed_vectors), default=len(AXES))
    nearest_profile = min((hamming(chosen_vector, item) for item in profile_vectors), default=len(AXES))
    nearest_recent = min((hamming(chosen_vector, item) for item in recent_vectors), default=len(AXES))
    reason = [
        "synthesized_new_vector=true",
        "not_equal_to_any_installed_factor_vector=true",
        "not_equal_to_any_static_direction_vector=true",
        f"require_new_direction_per_round={str(REQUIRE_NEW_DIRECTION_PER_ROUND).lower()}",
        f"recent_direction_exclude_limit={RECENT_DIRECTION_EXCLUDE_LIMIT}",
        f"recent_run_vectors_scanned={len(recent_vectors)}",
        "not_equal_to_any_recent_run_vector=true",
        f"vector_key={vector_key(chosen_vector)}",
        f"active_axes={', '.join(axes)}",
        f"rare_axes_in_installed_library={', '.join(rare_axes) or 'none'}",
        f"nearest_installed_vector_distance={nearest_installed}",
        f"nearest_recent_run_vector_distance={nearest_recent}",
        f"nearest_static_direction_distance={nearest_profile}",
    ]
    dynamic = {
        **chosen,
        "generated_from_installed_feature_vectors": True,
        "novel_feature_vector": True,
        "installed_factor_count": factor_count,
        "installed_cluster_count": len(clusters),
        "selection_reason": reason,
        "axis_counts": {axis: axis_counts[axis] for axis, _tokens in FEATURE_RULES},
    }
    return dynamic


def write_markdown(path: Path, direction: dict) -> None:
    lines = [
        "# Next Factor Direction",
        "",
        f"- title: {direction.get('title', '')}",
        f"- cluster: {direction.get('cluster', '')}",
        f"- installed factors scanned: {direction.get('installed_factor_count', 0)}",
        f"- installed clusters scanned: {direction.get('installed_cluster_count', 0)}",
        f"- data: {direction.get('data', '')}",
        f"- mechanism: {direction.get('mechanism', '')}",
        f"- avoid: {direction.get('avoid', '')}",
        "",
        "## Selection Reason",
        "",
    ]
    lines.extend(f"- {item}" for item in direction.get("selection_reason", []))
    lines.extend(["", "## Vector", ""])
    vector = direction.get("vector", {})
    for axis, _tokens in FEATURE_RULES:
        lines.append(f"- {axis}: {vector.get(axis, 0)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="derive next factor direction from installed feature vectors")
    parser.add_argument("--work-dir", type=Path, default=Path("/mnt/storage/work/hwang"))
    parser.add_argument("--out", type=Path, default=Path("output/next_factor_direction.json"))
    parser.add_argument("--md-out", type=Path, default=Path("output/next_factor_direction.md"))
    args = parser.parse_args()

    rows = [summarize_source(path) for path in factor_sources(args.work_dir)]
    clusters = cluster_rows(rows)
    profiles = load_direction_profiles()
    if not profiles:
        raise SystemExit("no direction profiles available")
    direction = choose_direction(rows, clusters, profiles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(direction, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(args.md_out, direction)
    print(f"wrote {args.out} and {args.md_out}")
    print(f"next_direction={direction.get('title')} cluster={direction.get('cluster')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
