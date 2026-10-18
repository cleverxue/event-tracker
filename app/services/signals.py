# -*- coding: utf-8 -*-
"""signals.csv 读写与收盘状态回填（规格 4.3 / 5.1 第 7 步）。

schema（2026-10-18 口径，触发条件修订为 MACD 柱放大 + CCI<-100 后，
金叉布尔改为记录原始值，便于人工复核）：
  trigger_time, symbol, event_id, current_price, ref_price_used,
  ref_price_type, deviation,
  macd_hist_at_trigger, macd_hist_prev_at_trigger, cci_at_trigger,  # 14:30 瞬时值
  macd_hist_at_close, macd_hist_prev_at_close, cci_at_close,        # 次日 16:00 回填
  notified, notified_time                               # 推送结果与推送时刻

5.1 第 7 步：16:00 任务回填前一日 signals 的收盘金叉状态——
用触发日（=16:00 任务眼中的"前一日"）的完整收盘日线重新计算
MACD/CCI，写入对应信号。仅用于复盘"瞬时金叉是否在收盘时保持"，
不改变任何触发状态。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime

import pandas as pd

from app import config

log = logging.getLogger(__name__)

SIGNALS_LOCK = threading.Lock()

SIGNALS_COLUMNS = [
    "trigger_time", "symbol", "event_id", "current_price", "ref_price_used",
    "ref_price_type", "deviation",
    # 触发时技术条件原始值（2026-10-18 口径，人工复核用；MACD 柱口径）
    "macd_hist_at_trigger", "macd_hist_prev_at_trigger", "cci_at_trigger",
    # 次日 16:00 收盘口径回填（复盘审计：收盘时是否仍满足条件）
    "macd_hist_at_close", "macd_hist_prev_at_close", "cci_at_close",
    "notified", "notified_time",
]


def load_signals() -> pd.DataFrame:
    """读 signals.csv；文件不存在返回空表（列齐）。"""
    if not config.SIGNALS_PATH.exists():
        return pd.DataFrame(columns=SIGNALS_COLUMNS)
    df = pd.read_csv(config.SIGNALS_PATH, dtype={"symbol": str, "event_id": str})
    for col in SIGNALS_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[SIGNALS_COLUMNS]


def append_signal(row: dict) -> None:
    """追加一条信号（带锁，防定时任务与手动触发并发写）。"""
    with SIGNALS_LOCK:
        df = load_signals()
        new = pd.DataFrame([{c: row.get(c, pd.NA) for c in SIGNALS_COLUMNS}])
        df = pd.concat([df, new], ignore_index=True)
        df.to_csv(config.SIGNALS_PATH, index=False, encoding="utf-8-sig")
        log.info("signals.csv 追加: %s %s", row.get("symbol"), row.get("trigger_time"))


def _pending_close_backfill(df: pd.DataFrame) -> pd.DataFrame:
    """筛出需要回填收盘状态的行：收盘字段为空且触发时间早于今天。"""
    if df.empty:
        return df
    trigger = pd.to_datetime(df["trigger_time"], errors="coerce")
    today = pd.Timestamp(date.today())
    pending = df["cci_at_close"].isna() & (trigger < today)
    return df[pending.fillna(False)]


def backfill_close_states(today: date | None = None) -> dict:
    """回填前一日 signals 的 MACD柱/CCI 收盘值（规格 5.1-7，2026-10-18 口径）。

    对每条待回填信号：取其触发日，用该日**完整收盘日线**（不加临时 bar）
    重算 MACD/CCI 最后一点的原始值。拉不到日线的信号保留空值，
    下个 16:00 任务继续尝试。
    """
    from app.services import indicators

    today = today or date.today()
    with SIGNALS_LOCK:
        df = load_signals()
        pending = _pending_close_backfill(df)
        if pending.empty:
            return {"pending": 0, "filled": 0, "failed": 0}

        # 按 (symbol, 触发日) 去重，同一只股票同一天只算一次
        tasks = {}
        for idx, row in pending.iterrows():
            ts = pd.to_datetime(row["trigger_time"])
            key = (row["symbol"], ts.strftime("%Y%m%d"))
            tasks.setdefault(key, []).append(idx)

        filled = failed = 0
        for (symbol, day_ymd), idxs in tasks.items():
            states = indicators.fetch_and_compute(symbol, temp_bar=None)
            if states is None or states["macd_hist"] is None:
                failed += 1
                log.warning("信号收盘回填失败（无日线/指标）: %s %s", symbol, day_ymd)
                continue
            for idx in idxs:
                df.loc[idx, "macd_hist_at_close"] = states["macd_hist"]
                df.loc[idx, "macd_hist_prev_at_close"] = states["macd_hist_prev"]
                df.loc[idx, "cci_at_close"] = states["cci"]
                filled += 1
            log.info("信号收盘回填: %s %s → MACD柱=%.4f(前值%.4f) CCI=%.1f",
                     symbol, day_ymd, states["macd_hist"],
                     states["macd_hist_prev"] or float("nan"),
                     states["cci"])

        if filled:
            df.to_csv(config.SIGNALS_PATH, index=False, encoding="utf-8-sig")
        summary = {"pending": len(pending), "filled": filled,
                   "failed": failed, "tasks": len(tasks)}
        log.info("信号收盘回填完成: %s", summary)
        return summary
