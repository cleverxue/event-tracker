# -*- coding: utf-8 -*-
"""一次性清理：watchlist 仅保留沪深主板 + 创业板（需求方 2026-10-17 确认）。

背景：M1 建库时选股范围为全部 A 股（含科创板 688、北交所 430/830/870/920），
现收紧为沪深主板 + 创业板。本脚本把存量 watchlist.parquet 中范围外的行剔除。

安全约定（个人数据，谨慎为上）：
  1. 先整表备份到 data/watchlist_pre_board_filter_YYYYMMDD.parquet 再动刀
  2. 走 storage 原子写（临时文件 + replace），运行中的服务读取不受影响
  3. timeline.parquet 不动——它是状态迁移审计底稿，且 API 只按 watchlist
     成员展示，被剔除事件的 timeline 行自然不可见（如需物理清理可另行处理）
  4. 当日快照若存在则同步刷新，保证快照与正表一致

用法：
  python scripts/filter_boards.py            # 备份 + 清理
  python scripts/filter_boards.py --dry-run  # 只看报告不动数据
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.storage import store  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("filter_boards")


def _board_of(symbol: str) -> str:
    """代码段 → 板块名（报告用）。"""
    code = str(symbol).zfill(6)
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("430", "830", "870", "920")):
        return "北交所"
    if code.startswith(("900", "200")):
        return "B股"
    if config.is_allowed_symbol(code):
        return "范围内"
    return "其他"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只出报告不动数据")
    args = ap.parse_args()

    wl = store.load_watchlist()
    if wl.empty:
        print("watchlist 为空，无需清理")
        return

    keep_mask = wl["symbol"].map(config.is_allowed_symbol)
    drop = wl[~keep_mask]
    kept = wl[keep_mask]

    print(f"watchlist 总行数: {len(wl)}")
    print(f"范围内（沪深主板+创业板）: {len(kept)}")
    print(f"范围外待剔除: {len(drop)}")
    if not drop.empty:
        dist = drop["symbol"].map(_board_of).value_counts().to_dict()
        print(f"范围外板块分布: {dist}")
        print("范围外示例（前 10 只）:")
        for sym in sorted(drop["symbol"].unique())[:10]:
            print(f"  {sym}")
    # 状态分布（确认剔除面）
    if not drop.empty:
        print(f"范围外行的 status 分布: {drop['status'].value_counts().to_dict()}")

    if args.dry_run or drop.empty:
        if drop.empty:
            print("无需剔除，数据已是目标范围")
        return

    # 1. 备份（带日期，防重复运行覆盖好备份）
    backup = config.DATA_DIR / (
        f"watchlist_pre_board_filter_"
        f"{pd.Timestamp.today().strftime('%Y%m%d')}.parquet")
    wl.to_parquet(backup, index=False)
    print(f"已备份 -> {backup}")

    # 2. 过滤 + 原子写
    store.save_watchlist(kept)
    print(f"已写回 {config.WATCHLIST_PATH}（{len(kept)} 行）")

    # 3. 当日快照同步刷新（保持快照与正表一致）
    today_snap = config.SNAPSHOT_DIR / (
        f"watchlist_{pd.Timestamp.today().strftime('%Y%m%d')}.parquet")
    if today_snap.exists():
        kept.to_parquet(today_snap, index=False)
        print(f"已刷新当日快照 -> {today_snap}")

    # 4. timeline 孤儿行报告（不动数据，仅告知）
    tl = store.load_timeline()
    if not tl.empty:
        live_ids = set(kept["event_id"])
        orphans = int((~tl["event_id"].isin(live_ids)).sum())
        if orphans:
            print(f"提示: timeline 有 {orphans} 行属于被剔除事件（审计底稿保留，"
                  f"API 不可见；如需物理清理请手动处理）")


if __name__ == "__main__":
    main()
