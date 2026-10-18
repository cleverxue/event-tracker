# -*- coding: utf-8 -*-
"""M1 主脚本：构建 watchlist 雏形（规格 5.1 的批量回填版）。

流程：
  1. 三关键词分开拉全市场公告，disclosure_id 去重（announcement.search_families）
  2. 标题分类，只对 initial（初始公告）下载原文（进度/完成/终止类留给 M2 状态机）
  3. 规则抽取价格字段（extract.extract_price），price_type=none 的留待 M3 Ollama 回填
  4. tracking_start 对齐下一交易日，tracking_expiry 按事件族窗口计算
  5. 写 data/watchlist.parquet（与既有数据按 (symbol, event_id) 合并）
     + data/m1_extraction_audit.csv（全量抽取审计，供 sample_check 抽检）

用法：
  python build_watchlist.py --start 20251017 --end 20261017   # 过去一年
  python build_watchlist.py --start 20261010 --end 20261016 --limit 200  # 烟雾测试
  python build_watchlist.py --start 20261010 --end 20261016 --titles-only # 只拉标题不下载
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app import config
from app.services import announcement, extract
from app.services.trading_calendar import load_calendar, next_trading_day

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("build_watchlist")

AUDIT_PATH = config.DATA_DIR / "m1_extraction_audit.csv"

WATCHLIST_COLUMNS = [
    "symbol", "event_id", "event_family", "price_type",
    "price_low", "price_high", "price_fixed", "reference_price",
    "avg_price", "avg_price_source", "publish_date",
    "tracking_start", "tracking_expiry", "status", "last_progress_check",
    "llm_extracted",
    "added_time", "entry_basis",   # 2026-10-18 新增：进入自选时间与依据
]


def run(start: str, end: str, limit: int | None = None,
        titles_only: bool = False, reset: bool = False) -> pd.DataFrame:
    calendar = load_calendar()
    if not calendar:
        log.warning("交易日历缺失，请先运行 scripts/refresh_trade_calendar.py；"
                    "tracking_start 将退化为工作日近似")

    # 1. 三关键词分开拉取 + 去重
    records = announcement.search_families(start, end)
    log.info("拉取到 %d 条公告，开始标题分类", len(records))

    # 2. 标题分类：initial 留下，其余进审计
    initial, skipped = [], []
    for rec in records:
        family, stage = extract.classify_announcement(rec["title"])
        rec["family"], rec["stage"] = family, stage
        if family and stage == "initial":
            initial.append(rec)
        else:
            skipped.append(rec)
    # 2.5 板块过滤：仅沪深主板+创业板（剔除科创板/北交所/B股等，需求方 2026-10-17 确认）
    _n_before = len(initial)
    initial = [r for r in initial if config.is_allowed_symbol(r["symbol"])]
    if _n_before != len(initial):
        log.info("板块过滤：剔除 %d 条范围外公告", _n_before - len(initial))
    if limit:
        initial = initial[:limit]  # 调试用：只处理前 N 条初始公告
    log.info("initial（初始公告，入 watchlist）%d 条；其余 %d 条（M2 状态机消费/未分类）",
             len(initial), len(skipped))

    # 3. 下载原文（initial only；缓存幂等，可断点续传）
    texts: dict[str, str] = {}
    if not titles_only:
        texts = announcement.download_texts(initial)

    # 4. 规则抽取 + 组装行
    rows, audit_rows = [], []
    added_at = datetime.now()  # 进入自选时刻（秒级，人工复核锚点）
    for rec in initial:
        body = texts.get(rec["disclosure_id"], "")
        price = extract.extract_price(rec["title"], body)
        ref = extract.reference_price(price)
        pub = datetime.strptime(rec["ann_date"], "%Y%m%d").date()
        tstart = next_trading_day(pub, calendar)
        texpiry = tstart + timedelta(days=config.TRACKING_WINDOW_DAYS[rec["family"]])
        rows.append({
            "symbol": rec["symbol"],
            "event_id": rec["disclosure_id"],
            "event_family": rec["family"],
            "price_type": price.price_type,
            "price_low": price.price_low,
            "price_high": price.price_high,
            "price_fixed": price.price_fixed,
            "reference_price": ref if ref is not None else float("nan"),
            "avg_price": float("nan"),
            "avg_price_source": "",
            "publish_date": pub,
            "tracking_start": tstart,
            "tracking_expiry": texpiry,
            "status": "watching",
            "last_progress_check": pd.NaT,
            "llm_extracted": "",   # M3 起由 Ollama 回填（16:00 任务）
            "added_time": added_at,
            "entry_basis": f"M1 历史构建｜{rec['title']}",
        })
        audit_rows.append({
            "disclosure_id": rec["disclosure_id"],
            "symbol": rec["symbol"], "sec_name": rec["sec_name"],
            "ann_date": rec["ann_date"], "publish_ts": rec["publish_ts"],
            "title": rec["title"], "matched_keywords": "/".join(rec["matched_keywords"]),
            "family": rec["family"], "stage": rec["stage"],
            "price_type": price.price_type,
            "price_low": price.price_low, "price_high": price.price_high,
            "price_fixed": price.price_fixed, "reference_price": ref,
            "counterparty": extract.extract_counterparty(rec["title"], body),
            "share_ratio": extract.extract_share_ratio(rec["title"], body),
            "text_chars": len(body),
            "evidence": price.evidence,
            "source_url": rec["source_url"],
        })
    for rec in skipped:
        audit_rows.append({
            "disclosure_id": rec["disclosure_id"],
            "symbol": rec["symbol"], "sec_name": rec["sec_name"],
            "ann_date": rec["ann_date"], "publish_ts": rec["publish_ts"],
            "title": rec["title"], "matched_keywords": "/".join(rec["matched_keywords"]),
            "family": rec["family"] or "", "stage": rec["stage"] or "unclassified",
            "price_type": "", "price_low": None, "price_high": None,
            "price_fixed": None, "reference_price": None,
            "counterparty": "", "share_ratio": None,
            "text_chars": 0, "evidence": "",
            "source_url": rec["source_url"],
        })

    # 5. 落盘：watchlist 合并 + 审计表
    df = pd.DataFrame(rows)
    df = _normalize_dtypes(df)
    if config.WATCHLIST_PATH.exists() and not reset:
        old = pd.read_parquet(config.WATCHLIST_PATH)
        old = _normalize_dtypes(old)
        before = len(old)
        df = pd.concat([old, df], ignore_index=True) \
             .drop_duplicates(subset=["symbol", "event_id"], keep="first")
        log.info("与既有 watchlist 合并: %d + %d -> %d", before, len(rows), len(df))
    elif reset and config.WATCHLIST_PATH.exists():
        log.info("--reset：忽略既有 watchlist，从零重建")
    df.to_parquet(config.WATCHLIST_PATH, index=False)
    pd.DataFrame(audit_rows).to_csv(AUDIT_PATH, index=False, encoding="utf-8-sig")

    # 汇总
    log.info("watchlist 写入 %s（%d 行）", config.WATCHLIST_PATH, len(df))
    if len(df):
        fam_count = df["event_family"].value_counts().to_dict()
        type_count = df["price_type"].value_counts().to_dict()
        log.info("事件族分布: %s", fam_count)
        log.info("price_type 分布: %s", type_count)
    return df


def _normalize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """统一 watchlist 列序与类型（规格 4.1）。"""
    for col in ("publish_date", "tracking_start", "tracking_expiry"):
        if col in df:
            df[col] = pd.to_datetime(df[col])
    if "last_progress_check" in df:
        df["last_progress_check"] = pd.to_datetime(df["last_progress_check"])
    for col in ("price_low", "price_high", "price_fixed",
                "reference_price", "avg_price"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "llm_extracted" in df:
        df["llm_extracted"] = df["llm_extracted"].fillna("").astype(str)
    return df[WATCHLIST_COLUMNS] if set(WATCHLIST_COLUMNS) <= set(df.columns) else df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--limit", type=int, default=None,
                    help="只处理分类后的前 N 条初始公告（调试）")
    ap.add_argument("--titles-only", action="store_true",
                    help="只拉标题分类，不下载原文")
    ap.add_argument("--reset", action="store_true",
                    help="忽略既有 watchlist.parquet，从零重建（批量回填用）")
    args = ap.parse_args()
    df = run(args.start, args.end, limit=args.limit,
             titles_only=args.titles_only, reset=args.reset)
    print(f"\n完成: watchlist 共 {len(df)} 行 -> {config.WATCHLIST_PATH}")


if __name__ == "__main__":
    main()
