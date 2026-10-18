# -*- coding: utf-8 -*-
"""存储层：watchlist / timeline 的读写（线程安全，规格十-5）。

定时任务（M3 起）与手动触发 API 可能并发写同一文件，用两个独立 Lock
分别收敛 watchlist 与 timeline 的写路径；落盘走"临时文件 + replace"
原子替换，避免写一半被读取方看到残缺文件。
"""
from __future__ import annotations

import threading

import pandas as pd

from app import config

WATCHLIST_LOCK = threading.Lock()
TIMELINE_LOCK = threading.Lock()

WATCHLIST_COLUMNS = [
    "symbol", "event_id", "event_family", "price_type",
    "price_low", "price_high", "price_fixed", "reference_price",
    "avg_price", "avg_price_source", "publish_date",
    "tracking_start", "tracking_expiry", "status", "last_progress_check",
    "llm_extracted",
    # 2026-10-18 新增（需求方要求：记录进入自选的时间与依据，供人工复核）
    "added_time", "entry_basis",
]

TIMELINE_COLUMNS = [
    "event_id", "announcement_id", "publish_timestamp", "title",
    "matched_rule", "state_before", "state_after", "source_url",
]


def _atomic_write_parquet(df: pd.DataFrame, path) -> None:
    tmp = path.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)  # 同目录原子替换


def load_watchlist() -> pd.DataFrame:
    if not config.WATCHLIST_PATH.exists():
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)
    df = pd.read_parquet(config.WATCHLIST_PATH)
    # 兼容旧文件：缺列补齐（llm_extracted 为 M3 新增；added_time/entry_basis
    # 为 2026-10-18 新增，旧文件的存量行由 backfill_entry_info.py 补录）
    for col in WATCHLIST_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col in ("llm_extracted", "entry_basis") else pd.NA
    # added_time 可能是 object dtype（全 NaT 的列落 parquet 会退化），
    # 统一转 datetime64 保证下游 .dt 可用
    if "added_time" in df.columns:
        df["added_time"] = pd.to_datetime(df["added_time"], errors="coerce")
    return df[WATCHLIST_COLUMNS]


def save_watchlist(df: pd.DataFrame) -> None:
    """写 watchlist（加锁版：手动触发/单次写入口）。"""
    with WATCHLIST_LOCK:
        _save_watchlist_locked(df)


def _save_watchlist_locked(df: pd.DataFrame) -> None:
    """写 watchlist（调用方已持有 WATCHLIST_LOCK 时用，防重入死锁）。

    threading.Lock 不可重入：run_screen 的"读-改-写"复合操作整段持锁，
    内部若再走 save_watchlist 会自死锁（M3 实测：无异常无输出，
    只能强杀进程），故拆出此内部写法。
    """
    _atomic_write_parquet(df, config.WATCHLIST_PATH)


def load_timeline() -> pd.DataFrame:
    if not config.TIMELINE_PATH.exists():
        return pd.DataFrame(columns=TIMELINE_COLUMNS)
    return pd.read_parquet(config.TIMELINE_PATH)


def append_timeline(rows: list[dict]) -> int:
    """追加 timeline 行，按 (event_id, announcement_id, state_after) 去重。

    去重键保证管线可重复跑（幂等）：同一公告对同一事件的同一次状态迁移
    只留一条。返回实际新增行数。
    """
    if not rows:
        return 0
    with TIMELINE_LOCK:
        old = load_timeline()
        new = pd.DataFrame(rows)[TIMELINE_COLUMNS]
        merged = pd.concat([old, new], ignore_index=True).drop_duplicates(
            subset=["event_id", "announcement_id", "state_after"], keep="first")
        _atomic_write_parquet(merged, config.TIMELINE_PATH)
        return len(merged) - len(old)
