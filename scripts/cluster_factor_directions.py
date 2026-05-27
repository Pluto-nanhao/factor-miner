#!/usr/bin/env python3
"""Cluster existing Alpha factors by data-category feature vectors.

The output is meant to guide the next factor-mining prompt directions. It uses
only static source features, so it can run without gsim or market data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


DATASET_RE = re.compile(r"dr\.getData\(\s*['\"]([^'\"]+)['\"]\s*\)")

FEATURE_RULES = [
    ("daily_price", ("'open'", '"open"', "'close'", '"close"', "'high'", '"high"', "'low'", '"low"')),
    ("daily_volume_amount", ("'volume'", '"volume"', "'amount'", '"amount"', "AShareMoneyFlow", "moneyflow")),
    ("intraday_price_path", ("Interval5m.open", "Interval5m.close")),
    ("intraday_amount_path", ("Interval5m.amo",)),
    ("forecast_revenue", ("revenue_forecast_", ".revenue", "forecast_year", "forecast_quarter")),
    (
        "forecast_profitability",
        (
            "financial_summary_fore_", "income_statement_fore_", "balance_sheet_fore_annual",
            "cash_flow_statement_fore_annual", "finance_ratio_fore_annual",
            "ashareconsensusrollingdata_", "ashareincome", "asharebalancesheet", "asharecashflow",
            "equ_factor_pq", "equ_factor_derive", "equ_factor_growth",
            "IS01", "IS02", "IS23", "IS36", "IS42", "BS", "CF", "ID",
        ),
    ),
    ("forecast_revision", ("forecast_revision", "revision", "slope", "near_vs_far", "coverage", "stability")),
    ("cross_sectional_state", ("np.nanmean", "np.nanstd", "valid_idx", "breadth", "cross", "market")),
    ("own_history_baseline", ("di -", "history", "baseline", "lookback", "prev_", "past_")),
    ("event_time_segmentation", ("argmax", "peak", "open_", "close_", "morning", "afternoon", "bar", "segment", "auction", "STAGE_ONE", "STAGE_TWO")),
    ("nonlinear_distribution", ("entropy", "skew", "kurt", "rank", "percentile", "np.log", "np.square", "equ_factor_obos")),
    ("return_momentum", ("momentum", "follow", "continue", "continuation", "trend", "ret_fast", "ret_mid", "breakout", "equ_factor_trend")),
    ("return_reversal", ("reversal", "reverse", "overreaction", "repair", "bounce", "mean_revert", "contrarian", "pressure", "hf_daily_auction_table")),
    ("liquidity_regime", ("liquidity", "liq", "amount_share", "amo_share", "volume_share", "turnover", "absorption", "AShareMoneyFlow", "equ_factor_volume")),
    ("volatility_regime", ("volatility", "vol", "range", "abs_ret", "sigma", "std", "compression", "equ_factor_return")),
    ("price_volume_divergence", ("divergence", "mismatch", "price_volume", "price/amount", "amount-weighted", "weighted_price")),
    ("time_of_day_seasonality", ("opening", "closing", "midday", "morning", "afternoon", "am_", "pm_", "late_", "early_")),
    ("tail_extreme_focus", ("tail", "extreme", "quantile", "top", "bottom", "max", "min", "clip")),
    ("persistence_decay_control", ("decay", "smooth", "half_life", "persistent", "persistence", "rolling", "ema")),
]


def factor_sources(work_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in work_dir.glob("Alpha*/Alpha*.py")
        if not path.parent.name.startswith(".")
    )


def feature_vector(source: str) -> dict[str, int]:
    return {
        name: int(any(token in source for token in tokens))
        for name, tokens in FEATURE_RULES
    }


def vector_key(vector: dict[str, int]) -> str:
    return "".join(str(vector[name]) for name, _tokens in FEATURE_RULES)


def summarize_source(path: Path) -> dict:
    source = path.read_text(encoding="utf-8", errors="ignore")
    datasets = sorted(set(DATASET_RE.findall(source)))
    vector = feature_vector(source)
    active = [name for name, value in vector.items() if value]
    return {
        "factor": path.parent.name,
        "path": str(path),
        "datasets": datasets,
        "vector": vector,
        "active_axes": active,
        "cluster_key": vector_key(vector),
    }


def cluster_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["cluster_key"]].append(row)
    clusters = []
    for key, members in grouped.items():
        axis_counts = {
            name: sum(member["vector"][name] for member in members)
            for name, _tokens in FEATURE_RULES
        }
        representative_axes = [name for name, count in axis_counts.items() if count]
        dataset_counts: dict[str, int] = defaultdict(int)
        for member in members:
            for dataset in member["datasets"]:
                dataset_counts[dataset] += 1
        clusters.append(
            {
                "cluster_key": key,
                "size": len(members),
                "axes": representative_axes,
                "datasets": sorted(dataset_counts, key=lambda item: (-dataset_counts[item], item)),
                "members": members,
            }
        )
    return sorted(clusters, key=lambda item: (-item["size"], item["cluster_key"]))


def load_direction_profiles() -> list[dict]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    old_dynamic = os.environ.get("FACTOR_MINER_USE_DYNAMIC_DIRECTION")
    os.environ["FACTOR_MINER_USE_DYNAMIC_DIRECTION"] = "false"
    try:
        from src.baseline import local_factor_miner as miner
    except Exception:
        return []
    finally:
        if old_dynamic is None:
            os.environ.pop("FACTOR_MINER_USE_DYNAMIC_DIRECTION", None)
        else:
            os.environ["FACTOR_MINER_USE_DYNAMIC_DIRECTION"] = old_dynamic
    profiles = []
    for idx, profile in enumerate(miner.DIRECTION_PROFILES):
        active_axes = [
            axis
            for axis, value in zip(miner.DIRECTION_VECTOR_AXES, profile.get("vector", []))
            if value
        ]
        profiles.append(
            {
                "idx": idx,
                "title": profile.get("title", ""),
                "cluster": profile.get("cluster", ""),
                "data": profile.get("data", ""),
                "mechanism": profile.get("mechanism", ""),
                "avoid": profile.get("avoid", ""),
                "active_axes": active_axes,
                "vector": dict(zip(miner.DIRECTION_VECTOR_AXES, profile.get("vector", []))),
            }
        )
    return profiles


def direction_coverage(clusters: list[dict], profiles: list[dict]) -> list[dict]:
    existing_keys = {cluster["cluster_key"] for cluster in clusters}
    covered = []
    for profile in profiles:
        key = "".join(str(profile["vector"].get(name, 0)) for name, _tokens in FEATURE_RULES)
        covered.append({**profile, "cluster_key": key, "covered_by_existing_factor": key in existing_keys})
    return covered


def vector_distance(left: dict[str, int], right: dict[str, int]) -> int:
    return sum(abs(left.get(name, 0) - right.get(name, 0)) for name, _tokens in FEATURE_RULES)


def nearest_direction_family(row: dict, profiles: list[dict]) -> dict:
    if not profiles:
        return {}
    return min(
        profiles,
        key=lambda profile: (
            vector_distance(row["vector"], profile["vector"]),
            profile["idx"],
        ),
    )


def readme_path_for_factor(row: dict) -> Path | None:
    factor_dir = Path(row["path"]).parent
    preferred = factor_dir / f"Readme.Hwang.{row['factor']}.md"
    if preferred.exists():
        return preferred
    candidates = sorted(factor_dir.glob("*.md"))
    return candidates[0] if candidates else None


def factor_family_section(row: dict, family: dict, clusters: list[dict]) -> str:
    cluster_size = 0
    cluster_members: list[str] = []
    for cluster in clusters:
        if cluster["cluster_key"] == row["cluster_key"]:
            cluster_size = cluster["size"]
            cluster_members = [member["factor"] for member in cluster["members"][:8]]
            break
    distance = vector_distance(row["vector"], family.get("vector", {})) if family else 0
    family_title = family.get("title", "unknown")
    family_cluster = family.get("cluster", "unknown")
    family_idx = family.get("idx", "")
    family_axes = family.get("active_axes", [])
    return "\n".join(
        [
            "<!-- factor-feature-family:start -->",
            "## 特征族归类",
            "",
            f"- 归属类别族：`D{family_idx}` {family_title} (`{family_cluster}`)",
            f"- 匹配距离：`{distance}`，越小代表源码特征向量越接近该类别族。",
            f"- 因子特征向量：`{row['cluster_key']}`",
            f"- 因子活跃轴：{', '.join(row['active_axes']) or 'none'}",
            f"- 类别族活跃轴：{', '.join(family_axes) or 'none'}",
            f"- 源码数据集：{', '.join(row['datasets']) or 'none'}",
            f"- 同特征簇规模：`{cluster_size}`",
            f"- 同簇样例：{', '.join(cluster_members) or 'none'}",
            "",
            "<!-- factor-feature-family:end -->",
        ]
    )


def replace_marked_section(text: str, section: str) -> str:
    start = "<!-- factor-feature-family:start -->"
    end = "<!-- factor-feature-family:end -->"
    start_idx = text.find(start)
    end_idx = text.find(end)
    if start_idx >= 0 and end_idx >= start_idx:
        end_idx += len(end)
        current = text[start_idx:end_idx]
        if current.strip() == section.strip():
            return text
        before = text[:start_idx].rstrip()
        after = text[end_idx:].lstrip()
        return before + "\n\n" + section + ("\n\n" + after if after else "\n")
    insert_at = text.find("\n## Full-Period Metrics")
    if insert_at < 0:
        insert_at = text.find("\n## Complete simsummary")
    if insert_at < 0:
        return text.rstrip() + "\n\n" + section + "\n"
    return text[:insert_at].rstrip() + "\n\n" + section + "\n" + text[insert_at:]


def update_factor_readmes(rows: list[dict], clusters: list[dict], profiles: list[dict]) -> list[Path]:
    updated = []
    for row in rows:
        readme_path = readme_path_for_factor(row)
        if readme_path is None:
            continue
        family = nearest_direction_family(row, profiles)
        section = factor_family_section(row, family, clusters)
        text = readme_path.read_text(encoding="utf-8", errors="replace")
        new_text = replace_marked_section(text, section)
        if new_text != text:
            readme_path.write_text(new_text, encoding="utf-8")
            updated.append(readme_path)
    return updated


def write_markdown(path: Path, clusters: list[dict], coverage: list[dict]) -> None:
    lines = [
        "# Factor Direction Feature Clusters",
        "",
        f"- factors scanned: {sum(cluster['size'] for cluster in clusters)}",
        f"- clusters: {len(clusters)}",
        "",
        "## Existing Factor Clusters",
        "",
    ]
    for cluster in clusters:
        examples = ", ".join(member["factor"] for member in cluster["members"][:8])
        lines.extend(
            [
                f"### {cluster['cluster_key']} size={cluster['size']}",
                f"- axes: {', '.join(cluster['axes']) or 'none'}",
                f"- datasets: {', '.join(cluster['datasets']) or 'none'}",
                f"- examples: {examples}",
                "",
            ]
        )
    if coverage:
        lines.extend(["## Candidate Direction Coverage", ""])
        for item in coverage:
            status = "covered" if item["covered_by_existing_factor"] else "open"
            lines.extend(
                [
                    f"- [{status}] D{item['idx']} {item['title']} ({item['cluster']})",
                    f"  axes: {', '.join(item['active_axes'])}",
                ]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="cluster factor source files by data-category vectors")
    parser.add_argument("--work-dir", type=Path, default=Path("/mnt/storage/work/hwang"))
    parser.add_argument("--out", type=Path, default=Path("output/factor_direction_clusters.md"))
    parser.add_argument("--json-out", type=Path, default=Path("output/factor_direction_clusters.json"))
    parser.add_argument("--update-readmes", action="store_true")
    args = parser.parse_args()

    rows = [summarize_source(path) for path in factor_sources(args.work_dir)]
    clusters = cluster_rows(rows)
    profiles = load_direction_profiles()
    coverage = direction_coverage(clusters, profiles)

    write_markdown(args.out, clusters, coverage)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps({"clusters": clusters, "direction_coverage": coverage}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {args.out} and {args.json_out}")
    print(f"factors={len(rows)} clusters={len(clusters)} open_directions={sum(not x['covered_by_existing_factor'] for x in coverage)}")
    if args.update_readmes:
        updated = update_factor_readmes(rows, clusters, profiles)
        print(f"updated_readmes={len(updated)}")
        for path in updated:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
