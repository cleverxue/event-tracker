# -*- coding: utf-8 -*-
"""交易日历服务。

跟踪起点 tracking_start 需对齐到"公告日后的下一交易日"。
数据源：腾讯指数（sh000001）日线推导，无重依赖，容器内可直接刷新。
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import date, timedelta

import requests

from app import config

log = logging.getLogger(__name__)

_QT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch_index_days(start: str, end: str, count: int = 800) -> list[str]:
    """拉取上证指数区间日线，返回 ['YYYY-MM-DD', ...]。失败返回空列表。"""
    param = f"sh000001,day,{start},{end},{count},qfq"
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            resp = requests.get(_QT_KLINE, params={"param": param},
                                headers=_HEADERS, timeout=20)
            node = resp.json().get("data", {}).get("sh000001", {})
            rows = node.get("day") or node.get("qfqday") or []
            days = [r[0] for r in rows if r and r[0]]
            if days:
                return days
        except Exception as exc:  # noqa: BLE001 - 网络异常统一降级
            last_err = exc
        time.sleep(min(2 ** attempt, 20))
    log.warning("交易日历刷新失败（腾讯指数日线）: %s", last_err)
    return []


def refresh_calendar(start: str = "2023-01-01", end: str = "2027-12-31") -> list[date]:
    """刷新 data/trade_calendar.csv，返回排序后的交易日列表。"""
    days: set[str] = set()
    # 区间可能超过单次上限，分两段拉取后合并
    for seg_start, seg_end in ((start, "2025-06-30"), ("2025-07-01", end)):
        days.update(_fetch_index_days(seg_start, seg_end))
    if not days:
        return []
    cal = sorted(date.fromisoformat(d) for d in days)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.TRADE_CALENDAR_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["trade_date"])
        for d in cal:
            writer.writerow([d.isoformat()])
    log.info("交易日历已刷新: %s ~ %s，共 %d 天 -> %s",
             cal[0], cal[-1], len(cal), config.TRADE_CALENDAR_PATH)
    return cal


def load_calendar() -> list[date]:
    """读取交易日历；文件缺失时返回空列表（调用方降级）。"""
    path = config.TRADE_CALENDAR_PATH
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return sorted(date.fromisoformat(row["trade_date"])
                      for row in csv.DictReader(fh) if row.get("trade_date"))


def _weekday_fallback(d: date) -> date:
    """日历缺失时的兜底：顺延到下一个工作日（不含法定节假日，仅应急）。"""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def next_trading_day(d: date, calendar: list[date] | None = None) -> date:
    """d 之后的下一交易日；日历无更远日期时走工作日兜底并记日志。"""
    cal = calendar if calendar is not None else load_calendar()
    for day in cal:
        if day > d:
            return day
    fallback = _weekday_fallback(d)
    log.warning("交易日历无 %s 之后的日期，兜底为工作日 %s", d, fallback)
    return fallback


def is_trading_day(d: date, calendar: list[date] | None = None) -> bool:
    """是否交易日；日历为空时退化为工作日判断。"""
    cal = calendar if calendar is not None else load_calendar()
    if not cal:
        return d.weekday() < 5
    return d in set(cal)
