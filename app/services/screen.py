# -*- coding: utf-8 -*-
"""16:00 任务服务层（规格 5.1）：全市场初筛 + watchlist 增量 + 信号回填。

流程（规格 5.1，勿简化）：
  1. 交易日判断（由调用方负责，批量回填可 --force）
  2. 三关键词分开拉当日公告，disclosure_id 去重（announcement.search_families）
  3. 标题分类，initial 下载原文并规则抽取价格
  4. price_type="none" 的记录调宿主机 Ollama 回填，写独立字段
     llm_extracted（JSON），不覆盖规则抽取结果；Ollama 不可达跳过不阻塞
  5. 新事件追加 watchlist（status=watching，tracking_start 对齐下一交易日，
     tracking_expiry 按事件族窗口），按 (symbol, event_id) 与既有数据合并
  6. 写当日快照 data/snapshots/watchlist_YYYYMMDD.parquet（保留 90 天）
  7. 回填前一日 signals 的 MACD柱/CCI 收盘值
     （signals.backfill_close_states，仅复盘审计，不改变触发状态）

watchlist 写走 storage.save_watchlist（threading.Lock + 原子写），
防止与手动触发接口并发写冲突。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from app import config
from app.services import announcement, extract, ollama, signals
from app.services.trading_calendar import load_calendar, next_trading_day
from app.storage import store

log = logging.getLogger(__name__)

AUDIT_PATH = config.DATA_DIR / "m1_extraction_audit.csv"


def run_screen(day: date | None = None, with_ollama: bool = True,
               with_backfill: bool = True) -> dict:
    """执行一次 16:00 初筛，返回汇总统计。day 默认今天。"""
    day = day or date.today()
    ymd = day.strftime("%Y%m%d")
    calendar = load_calendar()
    if not calendar:
        log.warning("交易日历缺失，tracking_start 将退化为工作日近似")

    # 1. 三关键词分开拉取 + 去重（search_families 内部按关键词分拉）
    records = announcement.search_families(ymd, ymd)
    log.info("%s 拉取到 %d 条公告，开始标题分类", ymd, len(records))

    # 2. 分类：initial 留下，其余跳过（进度/完成类由 21:30 状态机消费）
    initial = []
    for rec in records:
        family, stage = extract.classify_announcement(rec["title"])
        rec["family"], rec["stage"] = family, stage
        if family and stage == "initial":
            initial.append(rec)
    # 2.5 板块过滤：仅沪深主板+创业板（剔除科创板/北交所，需求方 2026-10-17 确认）
    _n_before = len(initial)
    initial = [r for r in initial if config.is_allowed_symbol(r["symbol"])]
    if _n_before != len(initial):
        log.info("板块过滤：剔除 %d 条范围外公告（科创板/北交所/B股等）",
                 _n_before - len(initial))
    log.info("initial（初始公告）%d 条，其余 %d 条", len(initial),
             len(records) - len(initial))
    if not initial:
        summary = {"new_events": 0, "ollama_filled": 0, "snapshot": "",
                   "close_backfill": {}}
        _write_snapshot()
        if with_backfill:
            summary["close_backfill"] = signals.backfill_close_states(day)
        return summary

    # 3. 下载原文 + 规则抽取
    texts = announcement.download_texts(initial)
    added_at = datetime.now()  # 进入自选时刻（秒级，人工复核锚点）
    rows = []
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
            "llm_extracted": "",
            "added_time": added_at,
            # 依据 = 来源渠道 + 公告标题原文（复核时一眼看到"因何进来"）
            "entry_basis": f"16:00 初筛｜{rec['title']}",
        })

    # 4. Ollama 回填（仅 price_type=none；不可达降级不阻塞）
    ollama_filled = 0
    if with_ollama:
        for row, rec in zip(rows, initial):
            if row["price_type"] != "none":
                continue
            body = texts.get(rec["disclosure_id"], "")
            if not body:
                continue
            result = ollama.backfill_fields(rec["title"], body)
            if result:
                row["llm_extracted"] = ollama.to_json_str(result)
                ollama_filled += 1
        log.info("Ollama 回填完成: %d/%d 条 none 记录", ollama_filled,
                 sum(1 for r in rows if r["price_type"] == "none"))

    # 5. 合并进 watchlist（去重 (symbol, event_id)，既有行保留不动）
    log.info("开始合并 watchlist（新事件 %d 条）", len(rows))
    new_df = _normalize(pd.DataFrame(rows))
    # 读-改-写整段持锁（防与手动触发接口并发写）；内部写用
    # _save_watchlist_locked——Lock 不可重入，不能再走 save_watchlist
    with store.WATCHLIST_LOCK:
        old = _normalize(store.load_watchlist())
        before = len(old)
        merged = pd.concat([old, new_df], ignore_index=True) \
            .drop_duplicates(subset=["symbol", "event_id"], keep="first")
        added = len(merged) - before
        store._save_watchlist_locked(merged)
    log.info("watchlist 合并: %d + %d -> %d（新增 %d）", before, len(new_df),
             len(merged), added)

    # 6. 当日快照
    snapshot_name = _write_snapshot()

    # 7. 信号收盘状态回填（复盘审计，不改变触发状态）
    close_backfill = {}
    if with_backfill:
        close_backfill = signals.backfill_close_states(day)

    return {"new_events": added, "ollama_filled": ollama_filled,
            "snapshot": snapshot_name, "close_backfill": close_backfill}


def _write_snapshot() -> str:
    """写当日 watchlist 快照（规格 4.4），返回文件名。"""
    df = store.load_watchlist()
    name = f"watchlist_{date.today().strftime('%Y%m%d')}.parquet"
    path = config.SNAPSHOT_DIR / name
    df.to_parquet(path, index=False)
    log.info("快照已写: %s（%d 行）", path, len(df))
    return name


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一列序与类型（规格 4.1 + llm_extracted）。

    直接对 DataFrame 做列变换——曾用 to_dict('records') 往返，
    2037 行即触发 pandas 慢路径（>5 分钟无输出），已改原生操作。
    """
    df = df.copy()
    for col in ("publish_date", "tracking_start", "tracking_expiry"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    if "last_progress_check" in df.columns:
        df["last_progress_check"] = pd.to_datetime(df["last_progress_check"])
    if "added_time" in df.columns:
        df["added_time"] = pd.to_datetime(df["added_time"])
    for col in ("price_low", "price_high", "price_fixed",
                "reference_price", "avg_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "llm_extracted" not in df.columns:
        df["llm_extracted"] = ""
    df["llm_extracted"] = df["llm_extracted"].fillna("").astype(str)
    if "entry_basis" not in df.columns:
        df["entry_basis"] = ""
    df["entry_basis"] = df["entry_basis"].fillna("").astype(str)
    cols = ["symbol", "event_id", "event_family", "price_type",
            "price_low", "price_high", "price_fixed", "reference_price",
            "avg_price", "avg_price_source", "publish_date",
            "tracking_start", "tracking_expiry", "status",
            "last_progress_check", "llm_extracted", "added_time", "entry_basis"]
    return df[cols]
