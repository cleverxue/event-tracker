# -*- coding: utf-8 -*-
"""M2 验收脚本：timeline 一致性 + 均价数学核对 + 硬指标统计。

校验项（对照规格 5.2 与 M2 验收标准）：
1. timeline schema 与规格 4.2 逐列一致；
2. 每条状态迁移的公告标题与目标状态自洽（completed 的标题必含
   完成/实施结果/过户等词，in_progress 必含"进展"……）；
3. 公告日期不早于事件公告日（防跨事件混入）；
4. 状态只进不退（rank 校验）；
5. avg_price == 累计金额 / 累计股数（数学核对）；
6. 硬指标：至少 3 条记录成功回填 avg_price；
7. watchlist 的 avg_price_source 必须指向一条真实存在且属于同族
   progress/completed 阶段的公告。

用法：python m2_verify.py（在 20 只验证跑完后执行）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.services.progress import _STATE_RANK  # noqa: E402

REPORT_PATH = config.DATA_DIR.parent / "reports" / "m2_acceptance.md"

# state_after → 标题必须命中的关键词（与分类器阶段正则同源，粗粒度复核）
_STATE_TITLE_KW = {
    "in_progress": ("进展",),
    "completed": ("完成", "实施完毕", "实施结果", "回购结果", "过户", "登记完成"),
    "terminated": ("终止", "解除", "停止"),
    "failed": ("未实施", "未能实施", "终止", "届满", "失败"),
    "approved": ("批准", "获批", "核准", "审核通过", "审查", "过会", "无异议"),
}


def main() -> int:
    problems: list[str] = []
    tl = pd.read_parquet(config.TIMELINE_PATH)
    wl = pd.read_parquet(config.WATCHLIST_PATH)
    audit = pd.read_csv(config.DATA_DIR / "m2_progress_audit.csv", dtype=str).fillna("")

    # 1. schema
    expect = ["event_id", "announcement_id", "publish_timestamp", "title",
              "matched_rule", "state_before", "state_after", "source_url"]
    if list(tl.columns) != expect:
        problems.append(f"timeline schema 不符: {list(tl.columns)}")
    print(f"timeline 行数: {len(tl)}，事件数: {tl['event_id'].nunique()}")

    # 2+3+4. 逐事件校验
    ev_meta = wl.set_index("event_id")[["symbol", "event_family", "publish_date"]]
    transitions = 0
    for event_id, grp in tl.groupby("event_id", sort=False):
        grp = grp.reset_index(drop=True)
        if event_id not in ev_meta.index:
            problems.append(f"{event_id} 不在 watchlist 中")
            continue
        meta = ev_meta.loc[event_id]
        family, pub = meta["event_family"], meta["publish_date"]
        prev_state, prev_rank = "", 0
        for _, r in grp.iterrows():
            after = r["state_after"]
            # 4. 只进不退
            rank = _STATE_RANK[family].get(after)
            if rank is None:
                problems.append(f"{event_id} 状态 {after} 不在 {family} 状态机内")
            elif rank < prev_rank:
                problems.append(f"{event_id} 状态回退 {prev_state}->{after}")
            else:
                prev_rank = rank
            prev_state = after
            # 3. 公告日期 ≥ 事件公告日
            ts = str(r["publish_timestamp"])[:10]
            if ts < str(pub)[:10]:
                problems.append(f"{event_id} 公告 {ts} 早于事件公告日 {pub}")
            # 2. 标题自洽（种子行与状态未变行不查）
            if r["state_before"] and r["state_before"] != r["state_after"]:
                transitions += 1
                kws = _STATE_TITLE_KW.get(after, ())
                if kws and not any(k in str(r["title"]) for k in kws):
                    problems.append(
                        f"{event_id} 迁移到 {after} 但标题未含关键词: {str(r['title'])[:40]}")
        # 终态之后不得再有迁移行（粘滞）
        states = grp["state_after"].tolist()
        for i, s in enumerate(states[:-1]):
            if _STATE_RANK[family].get(s) == 3 and states[i + 1] != s:
                problems.append(f"{event_id} 终态 {s} 后仍有后续行")
                break

    # 5. 均价数学核对
    avg_rows = audit[(audit["avg_price"] != "") & (audit["shares"] != "")]
    math_bad = 0
    for _, r in avg_rows.iterrows():
        expect_avg = float(r["amount"]) / float(r["shares"])
        if abs(expect_avg - float(r["avg_price"])) > 1e-6:
            math_bad += 1
            problems.append(f"{r['event_id']} 均价数学不符: {r['avg_price']} != "
                            f"{r['amount']}/{r['shares']}")
    print(f"均价抽取 {len(avg_rows)} 条，数学不符 {math_bad} 条")

    # 6. 硬指标：≥3 条记录回填 avg_price
    filled = wl[wl["avg_price"].notna()]
    print(f"watchlist 回填 avg_price 的记录: {len(filled)} 条（要求 ≥3）")
    if len(filled) < 3:
        problems.append(f"avg_price 回填仅 {len(filled)} 条，未达 M2 硬指标 3 条")

    # 7. avg_price_source 溯源
    for _, r in filled.iterrows():
        src = audit[audit["announcement_id"] == r["avg_price_source"]]
        if src.empty:
            problems.append(f"{r['event_id']} avg_price_source {r['avg_price_source']} "
                            f"不在本次审计公告中")
        elif not set(src["stage"]) <= {"progress", "completed"}:
            problems.append(f"{r['event_id']} avg_price_source 阶段异常: {src['stage'].tolist()}")

    # 状态分布
    print(f"\nwatchlist 状态分布:\n{wl['status'].value_counts().to_string()}")
    print(f"状态迁移总数（含未变行）: {len(tl)}，真实迁移: {transitions}")

    # 报告
    lines = [
        "# M2 验收报告（20 只股票）",
        "",
        f"- timeline: {len(tl)} 行 / {tl['event_id'].nunique()} 个事件，"
        f"真实状态迁移 {transitions} 次",
        f"- avg_price 回填: **{len(filled)} 条**（硬指标 ≥3）",
        f"- 均价数学核对: {len(avg_rows)} 条抽取，不符 {math_bad} 条",
        f"- watchlist 状态分布: {wl['status'].value_counts().to_dict()}",
        "",
        "## 校验结果",
        "",
    ]
    if problems:
        lines.append(f"发现 {len(problems)} 个问题：")
        lines += [f"- {p}" for p in problems]
    else:
        lines.append("全部通过：schema 一致、标题与状态自洽、无跨事件混入、"
                     "状态只进不退、均价数学正确、溯源完整。")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 -> {REPORT_PATH}")
    if problems:
        print(f"\n[FAIL] {len(problems)} 个问题")
        for p in problems[:20]:
            print(" -", p)
        return 1
    print("\n[PASS] M2 自动验收全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
