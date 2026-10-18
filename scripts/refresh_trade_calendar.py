# -*- coding: utf-8 -*-
"""CLI：刷新交易日历 data/trade_calendar.csv。

数据源：腾讯指数（sh000001）日线推导，无重依赖，容器内可直接跑。
用法：python scripts/refresh_trade_calendar.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.trading_calendar import refresh_calendar  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")

if __name__ == "__main__":
    cal = refresh_calendar()
    if cal:
        print(f"交易日历刷新完成: {cal[0]} ~ {cal[-1]}，共 {len(cal)} 天")
    else:
        print("交易日历刷新失败（腾讯指数日线不可达），保留原文件")
        sys.exit(1)
