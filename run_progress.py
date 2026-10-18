# -*- coding: utf-8 -*-
"""M2 主脚本：自选股公告进展 + 状态机 + 均价回填（规格 5.2 的批量版）。

用法：
  python run_progress.py --limit 20            # 20 只股票验证（族间轮询取样）
  python run_progress.py --symbol 600519       # 单只股票
  python run_progress.py --family 回购         # 只跑回购族
  python run_progress.py                       # 全量 watching/triggered（量很大，慎用）
  python run_progress.py --limit 20 --force    # 非交易日也跑（验证/回填用）

默认判断交易日（非交易日直接退出，与 21:30 任务一致），--force 跳过判断。
产出：data/timeline.parquet（状态迁移）+ watchlist 的 avg_price/status 回填
      + data/m2_progress_audit.csv（逐条公告的抽取审计，供人工核对）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.services import progress  # noqa: E402
from app.services.trading_calendar import is_trading_day  # noqa: E402
from app.storage import store  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("run_progress")


def reset_progress() -> None:
    """重置 M2 管理的字段并清空 timeline/审计（重验证用，谨慎！）。

    status 非 watching 的记录回到 watching、avg_price/avg_price_source/
    last_progress_check 清空——仅用于开发期反复验证，生产数据勿用。
    """
    wl = store.load_watchlist()
    if wl.empty:
        print("watchlist 为空，无需重置")
        return
    touched = wl["status"] != "watching"
    wl.loc[touched, "status"] = "watching"
    wl["avg_price"] = float("nan")
    wl["avg_price_source"] = ""
    wl["last_progress_check"] = pd.NaT
    store.save_watchlist(wl)
    for p in (config.TIMELINE_PATH, progress.AUDIT_PATH):
        if p.exists():
            p.unlink()
    print(f"已重置 {int(touched.sum())} 条记录的状态字段，清空 timeline 与审计表")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="最多处理 N 只（symbol+family 组），按公告日从早到晚、族间轮询")
    ap.add_argument("--symbol", action="append", default=None,
                    help="只处理指定股票（可多次）")
    ap.add_argument("--family", action="append", default=None,
                    choices=list(config.EVENT_FAMILIES),
                    help="只处理指定事件族（可多次）")
    ap.add_argument("--force", action="store_true",
                    help="非交易日也执行（验证/批量回填用）")
    ap.add_argument("--reset-progress", action="store_true",
                    help="先重置 M2 字段并清空 timeline 再跑（重验证用，谨慎）")
    args = ap.parse_args()

    if args.reset_progress:
        reset_progress()

    if not args.force and not is_trading_day():
        log.info("非交易日，退出（--force 可强制执行）")
        return

    summary = progress.run_progress(symbols=args.symbol,
                                    families=args.family,
                                    limit=args.limit)
    print(f"\n完成: {summary}")
    print(f"timeline -> {config.TIMELINE_PATH}")
    print(f"审计明细 -> {progress.AUDIT_PATH}")


if __name__ == "__main__":
    main()
