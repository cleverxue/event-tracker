# -*- coding: utf-8 -*-
"""触发引擎与飞书通知回归测试（M4）。

覆盖：
- 板块过滤：沪深主板+创业板放行，科创板/北交所/B股拦截（config.is_allowed_symbol）
- 参照价优先级：avg_price > price_fixed > range 中值；ceiling/none 语义
- 偏离度公式与 1% 门控（严格大于）
- 触发判定矩阵：MACD柱放大 + CCI<-100（2026-10-18 口径）、ceiling 型仅技术条件、无参照价跳过
- 飞书签名已知向量（HMAC-SHA256，官方口径 2026-10-18 修正）
- 卡片结构：header"信号触发"、必备字段、底部原文链接
- 原文直链推导：finalpage/日期/announcementId.PDF
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.services import feishu, tracker  # noqa: E402

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"  ✓ {name}")
    else:
        failures += 1
        print(f"  ✗ {name} {detail}")


def _row(**kw) -> pd.Series:
    """构造 watchlist 行（默认值 + 覆盖）。"""
    base = {"symbol": "600519", "event_id": "1", "price_type": "range",
            "price_low": 10.0, "price_high": 12.0, "price_fixed": None,
            "avg_price": float("nan")}
    base.update(kw)
    return pd.Series(base)


print("== 板块过滤（仅沪深主板+创业板）==")
for code, expect in [("600519", True), ("601888", True), ("603501", True),
                     ("605188", True), ("000001", True), ("001979", True),
                     ("002594", True), ("003816", True), ("300750", True),
                     ("301301", True), ("600519.SH", True),
                     ("688111", False), ("689009", False),   # 科创板
                     ("430047", False), ("830799", False),   # 北交所
                     ("870204", False), ("920020", False),   # 北交所新段
                     ("900901", False), ("200002", False)]:  # B 股
    check(f"{code} -> {expect}", config.is_allowed_symbol(code) == expect)

print("== 参照价优先级 ==")
ref, rtype = tracker.resolve_ref_price(
    _row(price_type="range", price_low=10, price_high=12, avg_price=11.5))
check("avg_price 优先", ref == 11.5 and rtype == "avg")
ref, rtype = tracker.resolve_ref_price(
    _row(price_type="fixed", price_fixed=15.0))
check("fixed 次之", ref == 15.0 and rtype == "fixed")
ref, rtype = tracker.resolve_ref_price(
    _row(price_type="range", price_low=10, price_high=12))
check("range 取中值", ref == 11.0 and rtype == "range")
ref, rtype = tracker.resolve_ref_price(
    _row(price_type="ceiling", price_high=30.0))
check("ceiling 返回上限价但标记辅助", ref == 30.0 and rtype == "ceiling")
ref, rtype = tracker.resolve_ref_price(_row(price_type="none"))
check("none 无均价 -> 无参照价", ref is None and rtype == "none")

print("== 偏离度公式 ==")
check("现价低于参照价 -> 正偏离",
      abs(tracker.calc_deviation(11.0, 10.0) - 0.1) < 1e-12)
check("现价等于参照价 -> 0", tracker.calc_deviation(10.0, 10.0) == 0.0)
check("现价高于参照价 -> 负偏离", tracker.calc_deviation(10.0, 11.0) < 0)
check("当前价为 0 -> None", tracker.calc_deviation(10.0, 0.0) is None)
check("参照价 None -> None", tracker.calc_deviation(None, 10.0) is None)

print("== 触发判定矩阵（2026-10-18 口径：MACD柱放大 + CCI<-100）==")
# 门控型（fixed/range）：偏离度>1% 且 MACD柱较前一日放大 且 CCI<-100
ok, why = tracker.should_trigger(0.02, True, -120, "fixed")
check("偏离2%+MACD柱放大+CCI-120 -> 触发", ok, why)
ok, why = tracker.should_trigger(0.02, True, -99.9, "fixed")
check("CCI 未低于 -100 -> 不触发", not ok, why)
ok, why = tracker.should_trigger(0.02, True, -100.0, "fixed")
check("CCI 恰 -100 不触发（严格小于）", not ok, why)
ok, why = tracker.should_trigger(0.02, False, -120, "fixed")
check("MACD柱未放大 -> 不触发", not ok, why)
ok, why = tracker.should_trigger(0.02, True, None, "fixed")
check("CCI 缺失 -> 不触发", not ok, why)
ok, why = tracker.should_trigger(0.005, True, -120, "range")
check("偏离 0.5% 不足 1% -> 不触发", not ok, why)
ok, why = tracker.should_trigger(0.01, True, -120, "range")
check("偏离恰 1% 不触发（严格大于）", not ok, why)
ok, why = tracker.should_trigger(0.010001, True, -120, "range")
check("偏离略超 1% + 技术条件 -> 触发", ok, why)
# ceiling 型：技术条件满足即触发，偏离度不门控
ok, why = tracker.should_trigger(None, True, -120, "ceiling")
check("ceiling 型技术条件满足 -> 触发（偏离度仅展示）", ok, why)
ok, why = tracker.should_trigger(-0.5, True, -120, "ceiling")
check("ceiling 型负偏离仍技术条件即触发", ok, why)
ok, why = tracker.should_trigger(None, False, -120, "ceiling")
check("ceiling 型 MACD柱未放大 -> 不触发", not ok, why)
ok, why = tracker.should_trigger(None, True, 50, "ceiling")
check("ceiling 型 CCI 未超卖 -> 不触发", not ok, why)
# none 型无均价：任何情况不触发
ok, why = tracker.should_trigger(None, True, -120, "none")
check("none 型无参照价 -> 不触发", not ok, why)
# CCI 阈值配置可查
check("CCI_OVERSOLD 配置为 -100", config.CCI_OVERSOLD == -100)

print("== 飞书签名（官方口径，已知向量；2026-10-18 修正）==")
ts, s = feishu.sign("test-secret", timestamp=1700000000)
check("timestamp 原样返回", ts == 1700000000)
check("sign 已知向量",
      s == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84=", s)
check("sign 为 base64 字符集",
      all(c.isalnum() or c in "+/=" for c in s))
payload = feishu.build_payload({"header": {}}, secret="test-secret",
                               timestamp=1700000000)
check("载荷含 timestamp/sign/msg_type/card",
      payload["timestamp"] == "1700000000"
      and payload["msg_type"] == "interactive"
      and payload["sign"] == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="
      and payload["card"] == {"header": {}})

print("== 卡片结构（2026-10-18 口径：原始值展示）==")
sig = {"symbol": "600519", "event_family": "回购", "current_price": 1234.5,
       "ref_price_used": 1250.0, "ref_price_type": "avg",
       "deviation": -0.0124, "macd_hist_at_trigger": 0.1234,
       "macd_hist_prev_at_trigger": 0.0567, "cci_at_trigger": -132.5,
       "trigger_time": "2026-10-20 14:30:00"}
card = feishu.build_signal_card(sig, "http://static.cninfo.com.cn/x.PDF")
check("header 为'信号触发'",
      card["header"]["title"]["content"] == "信号触发")
body = str(card["elements"])
for kw in ("600519", "回购", "1234.50", "1250.00", "回填均价",
           "-1.24%", "MACD柱", "CCI", "0.1234", "0.0567", "-132.5",
           "放大", "低于 -100 为超卖", "信号给出时间"):
    check(f"正文含 {kw}", kw in body)
check("底部含巨潮原文链接",
      any(e.get("tag") == "note" and "static.cninfo.com.cn" in str(e)
          for e in card["elements"]))
card_no_url = feishu.build_signal_card(sig, "")
check("无链接时无 note 元素",
      not any(e.get("tag") == "note" for e in card_no_url["elements"]))

print("== 原文直链推导 ==")
url = tracker.source_url_of("1224790251", pd.Timestamp("2025-11-06"))
check("finalpage 形态",
      url == "http://static.cninfo.com.cn/finalpage/2025-11-06/1224790251.PDF",
      url)

print()
if failures:
    print(f"失败 {failures} 项")
    sys.exit(1)
print("全部通过")
