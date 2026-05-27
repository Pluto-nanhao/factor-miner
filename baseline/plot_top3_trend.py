#!/usr/bin/env python3
"""Plot top-3-per-version trend of a chosen backtest metric across baseline versions.

Globs ``output/baseline_<v>/*/metrics.json`` for each version, ranks experiments
by the chosen metric (descending), keeps the top 3, and draws one line per rank
position (rank-1, rank-2, rank-3) across versions.

Notes for future maintainers:
- The CJK -> English metric-name fallback (see ``METRIC_DISPLAY_FALLBACK`` /
  ``display_label``) is intentional. Many headless / minimal hosts do not ship
  a CJK font, which would otherwise render Chinese labels as tofu boxes. When a
  CJK font *is* installed, the original Chinese metric name is used as-is.
- The canonical output filename pattern is
  ``docs/baseline_v<lo>_to_v<hi>_top3_trend.png`` (single-version case collapses
  to ``docs/baseline_v<v>_top3_trend.png``). Please keep this convention; the
  result-doc template and trainer workflow refer to it by name.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parent.parent

METRIC_DISPLAY_FALLBACK = {
    "策略收益": "Strategy Return",
    "策略年化收益": "Annualized Return",
    "超额收益": "Excess Return",
    "基准收益": "Benchmark Return",
    "阿尔法": "Alpha",
    "贝塔": "Beta",
    "夏普比率": "Sharpe Ratio",
    "胜率": "Win Rate",
    "盈亏比": "Profit/Loss Ratio",
    "最大回撤": "Max Drawdown",
    "日均超额收益": "Avg Daily Excess Return",
    "超额收益最大回撤": "Max Drawdown of Excess Return",
    "超额收益夏普比率": "Sharpe of Excess Return",
    "日胜率": "Daily Win Rate",
    "盈利次数": "Winning Trades",
    "亏损次数": "Losing Trades",
    "信息比率": "Information Ratio",
    "策略波动率": "Strategy Volatility",
    "基准波动率": "Benchmark Volatility",
    "最大回撤区间": "Max Drawdown Period",
}


def _has_cjk_font() -> bool:
    cjk_keywords = ("noto", "cjk", "wqy", "hei", "song", "ming", "kai", "yahei")
    for path in fm.findSystemFonts():
        if any(k in path.lower() for k in cjk_keywords):
            return True
    return False


def display_label(metric: str) -> str:
    if _has_cjk_font():
        return metric
    fallback = METRIC_DISPLAY_FALLBACK.get(metric)
    return fallback if fallback else metric


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--versions",
        nargs="+",
        required=True,
        help="One or more baseline version tags, e.g. v1 v2 v3",
    )
    p.add_argument(
        "--metric",
        default="信息比率",
        help="Metric key (Chinese name) used to rank experiments. Default: 信息比率",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output PNG path. Defaults to docs/baseline_<lo>_to_<hi>_top3_trend.png",
    )
    p.add_argument(
        "--output-root",
        default=str(REPO_ROOT / "output"),
        help="Root that contains baseline_<v>/ directories. Default: <repo>/output",
    )
    return p.parse_args(argv)


def load_version_metrics(output_root: Path, version: str, metric: str) -> list[tuple[str, float]]:
    version_dir = output_root / f"baseline_{version}"
    rows: list[tuple[str, float]] = []
    if not version_dir.is_dir():
        print(f"[warn] missing version dir: {version_dir}", file=sys.stderr)
        return rows
    for metrics_path in sorted(version_dir.glob("*/metrics.json")):
        try:
            with metrics_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] cannot read {metrics_path}: {exc}", file=sys.stderr)
            continue
        if metric not in payload:
            print(f"[warn] {metrics_path} missing metric {metric!r}", file=sys.stderr)
            continue
        value = payload[metric]
        if not isinstance(value, (int, float)):
            print(f"[warn] {metrics_path} metric {metric!r} is not numeric: {value!r}", file=sys.stderr)
            continue
        exp_id = metrics_path.parent.name
        rows.append((exp_id, float(value)))
    rows.sort(key=lambda kv: kv[1], reverse=True)
    return rows[:3]


def default_out_path(versions: list[str]) -> Path:
    lo, hi = versions[0], versions[-1]
    name = f"baseline_{lo}_to_{hi}_top3_trend.png" if lo != hi else f"baseline_{lo}_top3_trend.png"
    return REPO_ROOT / "docs" / name


def plot_trend(
    versions: list[str],
    per_version: list[list[tuple[str, float]]],
    metric: str,
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(max(6.0, 1.6 * len(versions) + 4.0), 5.0))

    rank_series: list[list[float | None]] = [[None] * len(versions) for _ in range(3)]
    rank_labels: list[list[str | None]] = [[None] * len(versions) for _ in range(3)]
    for v_idx, rows in enumerate(per_version):
        for r_idx in range(3):
            if r_idx < len(rows):
                exp_id, value = rows[r_idx]
                rank_series[r_idx][v_idx] = value
                rank_labels[r_idx][v_idx] = exp_id

    colors = ["#d62728", "#1f77b4", "#2ca02c"]
    markers = ["o", "s", "^"]
    x_positions = list(range(len(versions)))

    for r_idx in range(3):
        ys_raw = rank_series[r_idx]
        xs_plot = [x for x, y in zip(x_positions, ys_raw) if y is not None]
        ys_plot = [y for y in ys_raw if y is not None]
        if not xs_plot:
            continue
        ax.plot(
            xs_plot,
            ys_plot,
            marker=markers[r_idx],
            color=colors[r_idx],
            linewidth=2.0,
            markersize=8,
            label=f"rank-{r_idx + 1}",
        )
        for x, y, lbl in zip(xs_plot, ys_plot, [rank_labels[r_idx][x] for x in xs_plot]):
            ax.annotate(
                f"{y:.4f}\n{lbl}",
                xy=(x, y),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    ax.set_xticks(x_positions)
    ax.set_xticklabels([f"baseline_{v}" for v in versions])
    ax.set_xlabel("baseline version")
    metric_label = display_label(metric)
    ax.set_ylabel(metric_label)
    ax.set_title(f"Top-3 per version trend — {metric_label}")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="best")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    versions: list[str] = args.versions
    output_root = Path(args.output_root).resolve()
    metric = args.metric

    per_version = [load_version_metrics(output_root, v, metric) for v in versions]
    non_empty = [rows for rows in per_version if rows]
    if not non_empty:
        print(
            f"[error] no experiments found for metric {metric!r} across versions {versions}",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.out).resolve() if args.out else default_out_path(versions)
    plot_trend(versions, per_version, metric, out_path)
    size = out_path.stat().st_size
    print(f"wrote {out_path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
