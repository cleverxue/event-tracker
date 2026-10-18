# -*- coding: utf-8 -*-
"""M1 抽检脚本（规格九：随机抽 50 条核对）。

核对项（规格 M1 验收标准）：
  A. tracking_start 全部晚于 publish_date（全表 100% 检查）
  B. price_type 误标率 < 10%（抽样检查）
       - 误标：标了非 none 但证据片段不含对应数字 / 关键词与类型矛盾
       - 漏标：标了 none 但缓存原文中明显存在价格语义
  C. reference_price 与 price 字段一致（公式复核）
  D. tracking_expiry = tracking_start + 事件族窗口
输出：reports/m1_sample_check.md（含 50 条样本明细，供人工核对原文）

用法：python sample_check.py [--n 50] [--seed 42]
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.services.extract import normalize_text  # noqa: E402

REPORT_PATH = config.PROJECT_ROOT / "reports" / "m1_sample_check.md"

# 宽松价格语义探测（独立于抽取器的实现，用于发现漏标）
RE_ANY_PRICE = re.compile(
    r"(?:不超过|不高于|不低于|价格为|转让价格为|上限(?:为|是)?|区间(?:为|是)?)"
    r"[^。；;]{0,15}?(\d+(?:\.\d+)?)元/股")

# 类型与证据关键词的对应关系（交叉验证用）
_TYPE_KEYWORDS = {
    "ceiling": r"不超过|不高于|上限",
    "fixed": r"价格为",
    "range": r"不低于|至|~|到",
}


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    wl = pd.read_parquet(config.WATCHLIST_PATH)
    # dtype=str：disclosure_id 必须保持字符串（如 00501 前导零、与 watchlist 的
    # 字符串 event_id join），否则 pandas 推断为 int64 导致静默失配
    audit = pd.read_csv(config.DATA_DIR / "m1_extraction_audit.csv", dtype=str)
    return wl, audit


def cached_text(disclosure_id: str) -> str:
    path = config.CACHE_DIR / f"{disclosure_id}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_formula(wl: pd.DataFrame) -> list[str]:
    """C/D 两项公式复核，返回问题描述列表。"""
    issues = []
    for _, r in wl.iterrows():
        # reference_price 公式
        pt, ref = r["price_type"], r["reference_price"]
        if pt == "fixed":
            expect = r["price_fixed"]
        elif pt == "range":
            expect = (r["price_low"] + r["price_high"]) / 2 \
                if pd.notna(r["price_low"]) and pd.notna(r["price_high"]) else None
        elif pt == "ceiling":
            expect = r["price_high"]
        else:
            expect = None
        if expect is None or pd.isna(expect):
            if not (pd.isna(ref) or ref is None):
                issues.append(f"{r['symbol']}/{r['event_id']} {pt} 类型参照价应空实为 {ref}")
        elif pd.isna(ref) or abs(float(ref) - float(expect)) > 1e-6:
            issues.append(f"{r['symbol']}/{r['event_id']} 参照价 {ref} != 公式值 {expect}")
        # tracking_expiry
        window = config.TRACKING_WINDOW_DAYS[r["event_family"]]
        expect_expiry = r["tracking_start"] + timedelta(days=window)
        if r["tracking_expiry"] != expect_expiry:
            issues.append(f"{r['symbol']}/{r['event_id']} 到期日 {r['tracking_expiry']} "
                          f"!= {expect_expiry}")
    return issues


def check_sample(wl: pd.DataFrame, audit: pd.DataFrame,
                 n: int, seed: int) -> tuple[list[dict], dict]:
    """B 项抽样检查 + A 项全表检查。返回 (问题列表, 统计)。"""
    audit_idx = audit.set_index("disclosure_id")
    rng = random.Random(seed)
    sample = wl.sample(n=min(n, len(wl)), random_state=seed) if len(wl) else wl
    # 固定顺序的伪随机：用 random_state 保证可复现
    problems, stats = [], {
        "sample_size": len(sample),
        "mislabeled": 0, "missed": 0, "value_mismatch": 0,
        "keyword_mismatch": 0, "empty_text": 0,
    }
    for _, r in sample.iterrows():
        did = r["event_id"]
        a = audit_idx.loc[did] if did in audit_idx.index else None
        evidence = str(a["evidence"]) if a is not None else ""
        text = cached_text(did)
        if not text:
            stats["empty_text"] += 1
            continue
        norm = normalize_text(text)

        if r["price_type"] != "none":
            # 值核对：抽取值必须出现在原文（range 核对上下界两个值）
            if r["price_type"] == "range":
                values = [r["price_low"], r["price_high"]]
            elif r["price_type"] == "fixed":
                values = [r["price_fixed"]]
            else:  # ceiling
                values = [r["price_high"]]
            values = [v for v in values if v is not None and pd.notna(v)]
            if len(values) != (2 if r["price_type"] == "range" else 1):
                problems.append(f"{r['symbol']}/{did} {r['price_type']} 缺价格值")
                stats["value_mismatch"] += 1
            else:
                norm_nocomma = norm.replace(",", "")
                for v in values:
                    if f"{float(v):g}" not in norm_nocomma \
                            and f"{float(v):.2f}" not in norm:
                        problems.append(f"{r['symbol']}/{did} 值 {v} 在原文中找不到")
                        stats["value_mismatch"] += 1
            # 关键词交叉验证
            kw = _TYPE_KEYWORDS.get(r["price_type"], "")
            if kw and evidence and not re.search(kw, normalize_text(evidence)):
                problems.append(f"{r['symbol']}/{did} {r['price_type']} 证据关键词不符: "
                                f"{evidence[:60]}")
                stats["keyword_mismatch"] += 1
        else:
            # 漏标：原文有明显价格语义但标了 none
            m = RE_ANY_PRICE.search(norm)
            if m:
                problems.append(f"{r['symbol']}/{did} 漏标: 原文存在价格语义 "
                                f"「{m.group(0)[:50]}」")
                stats["missed"] += 1

    # A 项：tracking_start 全部晚于 publish_date（全表）
    bad_start = wl[wl["tracking_start"] <= wl["publish_date"]]
    for _, r in bad_start.iterrows():
        problems.append(f"{r['symbol']}/{r['event_id']} tracking_start "
                        f"{r['tracking_start']} 未晚于 publish_date {r['publish_date']}")
    stats["bad_start"] = len(bad_start)
    return problems, stats


def _fmt_ref(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{float(v):.4g}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    wl, audit = load_data()
    print(f"watchlist {len(wl)} 行；抽检 {min(args.n, len(wl))} 条（seed={args.seed}）")

    formula_issues = check_formula(wl)
    problems, stats = check_sample(wl, audit, args.n, args.seed)

    mislabel = stats["value_mismatch"] + stats["keyword_mismatch"]
    rate = mislabel / stats["sample_size"] if stats["sample_size"] else 0.0

    # ---- 报告 ----
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    audit_idx = audit.set_index("disclosure_id")
    sample = wl.sample(n=min(args.n, len(wl)), random_state=args.seed) if len(wl) else wl
    lines = [
        "# M1 抽检报告", "",
        f"- watchlist 总数: {len(wl)}",
        f"- 抽样: {stats['sample_size']} 条（seed={args.seed}）",
        f"- tracking_start 未晚于 publish_date: {stats['bad_start']} 条（要求 0）",
        f"- price_type 误标: {mislabel} 条 → 误标率 {rate:.1%}（要求 <10%）",
        f"- 漏标（标 none 但原文有价格语义）: {stats['missed']} 条",
        f"- 原文缺失（下载失败）: {stats['empty_text']} 条",
        f"- reference_price 公式问题: {len(formula_issues)} 条",
        "",
        "## 问题明细", "",
    ]
    if problems or formula_issues:
        lines += [f"- {p}" for p in problems + formula_issues]
    else:
        lines.append("（无）")

    lines += ["", "## 样本明细（人工核对用）", "",
              "| # | symbol | 事件族 | price_type | 参照价 | 公告标题 | 证据片段 | 原文链接 |",
              "|---|---|---|---|---|---|---|---|"]
    for i, (_, r) in enumerate(sample.iterrows(), 1):
        a = audit_idx.loc[r["event_id"]] if r["event_id"] in audit_idx.index else None
        ev = str(a["evidence"])[:70].replace("|", "/").replace("\n", " ") if a is not None else ""
        title = str(r.get("title", "")) if "title" in r else ""
        if not title and a is not None:
            title = str(a["title"])
        url = str(a["source_url"]) if a is not None else ""
        lines.append(
            f"| {i} | {r['symbol']} | {r['event_family']} | {r['price_type']} "
            f"| {_fmt_ref(r['reference_price'])} | {title[:36]} | {ev} | [链接]({url}) |")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入 {REPORT_PATH}")
    print(f"\n===== 汇总 =====")
    print(f"tracking_start 违规: {stats['bad_start']}（要求 0）")
    print(f"price_type 误标率: {rate:.1%}（要求 <10%）")
    print(f"漏标: {stats['missed']}；原文缺失: {stats['empty_text']}")
    print(f"公式问题: {len(formula_issues)}")


if __name__ == "__main__":
    main()
