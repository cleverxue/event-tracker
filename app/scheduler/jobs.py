# -*- coding: utf-8 -*-
"""定时任务注册（规格 5.1 / 5.2 / 5.3 的调度入口，M3）。

三个 CronTrigger（Asia/Shanghai）：
  16:00 announcement_screen — 全市场初筛 + watchlist 增量 + 信号回填
  21:30 progress_analysis   — 自选股公告进展 + 状态机 + 均价回填
  14:30 tracker             — 触发引擎（即时金叉判定；引擎本体 M4 实现，
                               M3 先注册占位，非交易日与无 watching 时安全跳过）

所有任务先判断交易日，非交易日直接返回（规格十-5）。
每个任务独立 try/except：单个任务异常不影响调度器与其他任务。
"""
from __future__ import annotations

import logging
from datetime import date

from app import config
from app.services import progress, screen, signals
from app.services.trading_calendar import is_trading_day

log = logging.getLogger(__name__)


def job_announcement_screen(day: date | None = None,
                            force: bool = False) -> dict:
    """16:00 全市场初筛（规格 5.1）。force=True 绕过交易日判断（调试用）。"""
    day = day or date.today()
    if not force and not is_trading_day(day):
        log.info("16:00 初筛：%s 非交易日，跳过", day)
        return {"skipped": "non-trading-day"}
    log.info("16:00 初筛开始: %s", day)
    return screen.run_screen(day)


def job_progress_analysis(day: date | None = None,
                          force: bool = False) -> dict:
    """21:30 自选股公告进展（规格 5.2）。force=True 绕过交易日判断。"""
    day = day or date.today()
    if not force and not is_trading_day(day):
        log.info("21:30 进展分析：%s 非交易日，跳过", day)
        return {"skipped": "non-trading-day"}
    log.info("21:30 进展分析开始: %s", day)
    return progress.run_progress(today=day)


def job_tracker(day: date | None = None, force: bool = False) -> dict:
    """14:30 触发引擎（规格 5.3，M4 实现本体）。

    即时金叉判定 + 偏离度门控 + signals.csv 落库 + 飞书交互卡片；
    行情失败降级 mootdx，仍失败当日跳过并记日志，不发错误信号。
    force=True 绕过交易日判断（API 手动触发调试用）。
    """
    day = day or date.today()
    if not force and not is_trading_day(day):
        log.info("14:30 触发引擎：%s 非交易日，跳过", day)
        return {"skipped": "non-trading-day"}
    log.info("14:30 触发引擎开始: %s", day)
    from app.services import tracker
    return tracker.run_tracker(day=day, force=force)


def safe_run(fn, *args, **kwargs) -> dict:
    """任务包装：异常只记日志不上抛，保证调度器存活。"""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - 顶层兜底，保调度器
        log.exception("任务 %s 执行异常（已捕获，调度器继续）", fn.__name__)
        return {"error": fn.__name__}
