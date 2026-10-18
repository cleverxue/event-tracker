# -*- coding: utf-8 -*-
"""M4 主脚本：手动运行 14:30 触发引擎（调试/验收用）。

定时触发由 uvicorn 进程内调度器负责（每交易日 14:30）；本脚本用于：
  - 非交易日/盘后手动验证（--force）
  - 单只股票调试（--symbol）
  - 小规模抽样（--limit）
  - 干跑：完整判定但不写库不推送（--dry-run）
  - 只落信号不推飞书（--no-notify；飞书密钥未配时用）

用法：
  python run_tracker.py --force --limit 30            # 抽样 30 条事件验证
  python run_tracker.py --force --symbol 600519       # 单只股票
  python run_tracker.py --force --dry-run             # 全量干跑（不写库不推送）
  python run_tracker.py --force --no-notify           # 落信号但不推送

默认判断交易日（非交易日直接退出，与 14:30 任务一致），--force 跳过判断。
产出：data/signals.csv（信号明细）+ watchlist status=triggered 回写
      + logs/notify_failed.log（推送失败时）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.services import tracker  # noqa: E402
from app.services.trading_calendar import is_trading_day  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("run_tracker")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="最多评估 N 条事件（按代码+公告日排序取前 N，调试用）")
    ap.add_argument("--symbol", action="append", default=None,
                    help="只处理指定股票（可多次）")
    ap.add_argument("--force", action="store_true",
                    help="非交易日也执行（验证用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="干跑：完整判定但不写库、不推送")
    ap.add_argument("--no-notify", action="store_true",
                    help="落信号但不发飞书（密钥未配置/静默验证时用）")
    args = ap.parse_args()

    if not args.force and not is_trading_day():
        log.info("非交易日，退出（--force 可强制执行）")
        return

    summary = tracker.run_tracker(symbols=args.symbol, limit=args.limit,
                                  dry_run=args.dry_run,
                                  notify=not args.no_notify)
    print(f"\n完成: {summary}")
    print(f"信号明细 -> {config.SIGNALS_PATH}")


if __name__ == "__main__":
    main()
