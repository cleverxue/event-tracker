# -*- coding: utf-8 -*-
"""只读 API + 手动触发接口（规格第七章，无认证）。

  GET  /api/watchlist?status=&family=          列表，支持筛选
  GET  /api/watchlist/{symbol}/timeline        事件时间线
  GET  /api/signals?from=&to=                  历史信号（含触发时/收盘时对比字段）
  POST /api/trigger/run                         手动触发 14:30 引擎（调试用，M4）
  POST /api/progress/run                        手动触发 21:30 进展分析（调试用）
"""
from __future__ import annotations

import logging

import pandas as pd
from fastapi import APIRouter, Query

from app import config
from app.scheduler import jobs
from app.services import signals as signals_svc
from app.services.trading_calendar import is_trading_day
from app.storage import store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# 序列化辅助：parquet 的 date/datetime/NaN → JSON 友好值
# --------------------------------------------------------------------------- #
def _jsonable(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        rec = {}
        for col, val in row.items():
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, (pd.Timestamp,)):
                rec[col] = val.strftime("%Y-%m-%d %H:%M:%S")
            elif hasattr(val, "isoformat"):
                rec[col] = val.isoformat()
            else:
                rec[col] = val
        out.append(rec)
    return out


def _fmt_date(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


# --------------------------------------------------------------------------- #
# 只读接口
# --------------------------------------------------------------------------- #
@router.get("/watchlist")
def list_watchlist(status: str | None = Query(default=None),
                   family: str | None = Query(default=None)):
    """watchlist 列表，支持按 status / event_family 筛选。"""
    df = store.load_watchlist()
    if status:
        df = df[df["status"] == status]
    if family:
        df = df[df["event_family"] == family]
    df = _fmt_date(df, ("publish_date", "tracking_start", "tracking_expiry"))
    # added_time 保留时分秒（进入自选时刻是复核锚点）；NaT -> None
    if "added_time" in df.columns:
        df["added_time"] = pd.to_datetime(df["added_time"], errors="coerce")
        df["added_time"] = df["added_time"].dt.strftime(
            "%Y-%m-%d %H:%M:%S").where(df["added_time"].notna(), None)
    # last_progress_check 保留时分秒
    if "last_progress_check" in df.columns:
        df["last_progress_check"] = df["last_progress_check"].dt.strftime(
            "%Y-%m-%d %H:%M:%S").where(df["last_progress_check"].notna(), None)
    df = df.sort_values(["symbol", "publish_date"])
    return {"total": len(df), "items": _jsonable(df)}


@router.get("/watchlist/{symbol}/timeline")
def watchlist_timeline(symbol: str):
    """单只股票的公告时间线（该 symbol 全部事件，按时间正序）。"""
    sym = str(symbol).zfill(6)
    wl = store.load_watchlist()
    wl = wl[wl["symbol"] == sym]
    tl = store.load_timeline()
    tl = tl[tl["event_id"].isin(set(wl["event_id"]))]
    tl = tl.sort_values(["publish_timestamp"])
    return {"symbol": sym, "total": len(tl), "items": _jsonable(tl)}


@router.get("/signals")
def list_signals(date_from: str | None = Query(default=None, alias="from"),
                 date_to: str | None = Query(default=None, alias="to")):
    """历史信号，含 macd_hist_at_trigger/cci_at_trigger 与收盘回填值对比字段。"""
    df = signals_svc.load_signals()
    if not df.empty and date_from:
        df = df[pd.to_datetime(df["trigger_time"], errors="coerce")
                >= pd.Timestamp(date_from)]
    if not df.empty and date_to:
        df = df[pd.to_datetime(df["trigger_time"], errors="coerce")
                <= pd.Timestamp(date_to) + pd.Timedelta(days=1)]
    if not df.empty:
        df = df.sort_values("trigger_time", ascending=False)
    return {"total": len(df), "items": _jsonable(df)}


# --------------------------------------------------------------------------- #
# 手动触发（调试用）
# --------------------------------------------------------------------------- #
@router.post("/trigger/run")
def run_tracker():
    """手动触发 14:30 触发引擎（M4 已实现；force 绕过交易日判断）。

    全量 watching 候选的一轮完整判定（行情 → 偏离度 → 即时金叉 →
    信号落库 + 飞书）。单只/抽样调试建议用 run_tracker.py。
    """
    from datetime import date as _date
    return {"status": "ok", "trading_day": is_trading_day(_date.today()),
            "result": jobs.safe_run(jobs.job_tracker, force=True)}


@router.post("/progress/run")
def run_progress():
    """手动触发 21:30 进展分析（调试用：绕过交易日判断直接执行）。

    全量（watching 上千只）约 1-2 小时；小规模调试建议用
    run_progress.py --limit N。执行期间请保持请求不中断。
    """
    from datetime import date as _date
    from app.services import progress as progress_svc
    return {"status": "ok",
            "result": jobs.safe_run(progress_svc.run_progress,
                                    today=_date.today())}
