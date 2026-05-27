#!/usr/bin/env python3
"""Download factors.directory zh factor pages and classify local feasibility."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path


BASE = "https://factors.directory"
START = "https://factors.directory/zh"


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.links: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip = True
        attrs_d = dict(attrs)
        href = attrs_d.get("href")
        if href:
            self.links.append(href)
        if tag in {"h1", "h2", "h3", "p", "li", "br"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip = False
        if tag in {"h1", "h2", "h3", "p", "li"}:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "tinyKaggleClaw-factor-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def clean_text(raw: str) -> str:
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def parse_page(url: str, body: str) -> dict[str, object]:
    parser = TextParser()
    parser.feed(body)
    text = clean_text("".join(parser.text))
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line and line not in {"Factors Directory", "Quantitative Trading Factors", "nav.menu.open"}:
            title = line
            break
    category = ""
    for line in text.splitlines()[1:10]:
        if line.strip().endswith("因子") or line.strip() in {"技术因子", "情绪因子", "动量因子", "波动率因子", "流动性因子", "基础面因子"}:
            category = line.strip()
            break
    links = []
    for href in parser.links:
        full = urllib.parse.urljoin(url, href)
        parts = urllib.parse.urlparse(full)
        if parts.netloc == "factors.directory" and parts.path.startswith("/zh/factors/"):
            links.append(urllib.parse.urlunparse((parts.scheme, parts.netloc, parts.path, "", "", "")))
    return {"url": url, "title": title, "category": category, "text": text, "links": sorted(set(links))}


def feasibility(text: str, category: str) -> tuple[str, str]:
    lower = text.lower()
    unavailable = [
        "财报", "资产负债", "利润", "现金流", "每股", "ttm", "roe", "roa", "ebit", "市值",
        "股本", "换手率", "流通", "分析师", "评级", "公告", "新闻", "社交媒体", "期权",
        "债务", "收入", "应收", "存货", "账款", "净资产", "企业价值",
    ]
    if any(key in lower for key in unavailable) or category in {"基础面因子", "成长性因子", "质量因子", "价值因子", "规模因子"}:
        return "no", "needs fundamental/share-cap/sentiment fields unavailable in current framework"
    strict_intraday = ["5分钟", "分钟k线", "k线", "vwap", "成交额", "成交量", "开盘", "收盘", "最高", "最低"]
    price_volume = ["收益率", "动量", "反转", "波动", "相关", "成交量", "成交额", "价格", "振幅", "价量", "风险价值", "cvar", "var"]
    if any(key in lower for key in strict_intraday):
        return "yes", "can use 5m open/close/amo plus daily OHLCV/amount proxies"
    if any(key in lower for key in price_volume) and category in {"技术因子", "动量因子", "波动率因子", "流动性因子", "情绪因子"}:
        return "partial", "daily/intraday price-volume proxy possible; verify field assumptions"
    return "review", "unclear mapping; manual review needed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=800)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    start_body = fetch(START)
    start = parse_page(START, start_body)
    queue = deque(start["links"])
    seen: set[str] = set()
    pages: list[dict[str, object]] = []
    def fetch_page(url: str) -> tuple[str, dict[str, object] | None, str | None]:
        try:
            body = fetch(url)
            return url, parse_page(url, body), None
        except Exception as exc:  # noqa: BLE001
            return url, None, str(exc)

    workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and len(pages) < args.max_pages:
            batch = []
            while queue and len(batch) < workers and len(pages) + len(batch) < args.max_pages:
                url = queue.popleft()
                if url in seen:
                    continue
                seen.add(url)
                batch.append(url)
            if not batch:
                continue
            futures = [pool.submit(fetch_page, url) for url in batch]
            for future in as_completed(futures):
                url, page, err = future.result()
                if page is None:
                    print(f"fetch_failed {url}: {err}", flush=True)
                    continue
                feasible, reason = feasibility(str(page["text"]), str(page["category"]))
                page["feasible_5m"] = feasible
                page["feasible_reason"] = reason
                pages.append(page)
                for link in page["links"]:
                    if link not in seen:
                        queue.append(link)
                print(f"{len(pages):04d} {feasible:7s} {page['title']} {url}", flush=True)
            time.sleep(args.sleep)

    json_path = args.out_dir / "factors_directory_zh.json"
    json_path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = args.out_dir / "factors_directory_zh.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "category", "feasible_5m", "feasible_reason", "url"])
        writer.writeheader()
        for page in pages:
            writer.writerow({k: page.get(k, "") for k in writer.fieldnames})

    md_lines = ["# Factors Directory 5m Feasibility", ""]
    for label in ["yes", "partial", "review", "no"]:
        subset = [p for p in pages if p.get("feasible_5m") == label]
        md_lines.extend([f"## {label} ({len(subset)})", ""])
        for p in subset[:200]:
            md_lines.append(f"- {p.get('title')} [{p.get('category')}] - {p.get('feasible_reason')} - {p.get('url')}")
        md_lines.append("")
    (args.out_dir / "factors_directory_5m_feasibility.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
