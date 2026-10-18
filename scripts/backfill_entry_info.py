# -*- coding: utf-8 -*-
"""存量 watchlist 补录 added_time / entry_basis（2026-10-18 新增列一次性回填）。

诚实原则：存量 1692 行是 M1（2026-10-17）初建 + 板块清理时入库的，
**确切的进入时刻已无从考证**——故：
  - added_time：留空（NaT），不伪造时间；新建行（16:00 初筛）才有精确值
  - entry_basis：填「存量补录（2026-10-18）｜{公告标题}」，标题从 M1 审计
    CSV（m1_extraction_audit.csv）按 disclosure_id 关联；关联不到的标注
    「标题未记录，见 event_id 原文」

用法：
  python scripts/backfill_entry_info.py --dry-run   # 只看统计不写
  python scripts/backfill_entry_info.py             # 先备份再写
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.storage import store  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_entry_info")

STAMP = "2026-10-18"
AUDIT_PATH = config.DATA_DIR / "m1_extraction_audit.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="存量 watchlist 补录进入自选信息")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    wl = store.load_watchlist()
    total = len(wl)
    need = wl["added_time"].isna() | (wl["entry_basis"].fillna("") == "")
    n_need = int(need.sum())
    print(f"watchlist 共 {total} 行，待补录 {n_need} 行，已有记录 {total - n_need} 行")
    if n_need == 0:
        print("无需补录，退出")
        return

    # 标题关联：audit CSV 的 disclosure_id -> title
    title_map: dict[str, str] = {}
    if AUDIT_PATH.exists():
        audit = pd.read_csv(AUDIT_PATH, dtype=str)
        if {"disclosure_id", "title"} <= set(audit.columns):
            title_map = dict(zip(audit["disclosure_id"], audit["title"]))
            print(f"审计 CSV 提供 {len(title_map)} 条标题映射")
    else:
        print("警告：审计 CSV 不存在，标题将全部标注为未记录")

    mask = need
    titles = wl.loc[mask, "event_id"].astype(str).map(title_map)
    basis = titles.fillna("").radd("")  # 先取出
    basis = [
        f"存量补录（{STAMP}）｜{t if t else '标题未记录，见 event_id 原文'}"
        for t in titles.fillna("")
    ]
    wl.loc[mask, "added_time"] = pd.NaT  # 如实留空
    wl.loc[mask, "entry_basis"] = basis

    no_title = int(sum(1 for b in basis if "标题未记录" in b))
    print(f"补录预览：entry_basis {len(basis)} 条（其中标题未关联 {no_title} 条）")
    print(f"  added_time：全部留空（存量时刻无从考证，不伪造）")
    print(f"  示例：{basis[0] if basis else '-'}")

    if args.dry_run:
        print("dry-run：不写库")
        return

    backup = config.WATCHLIST_PATH.with_name(
        f"watchlist_pre_entryinfo_{datetime.now():%Y%m%d_%H%M%S}.parquet")
    backup.write_bytes(config.WATCHLIST_PATH.read_bytes())
    print(f"已备份原文件 -> {backup.name}")
    store.save_watchlist(wl)
    print("写库完成")


if __name__ == "__main__":
    main()
