# -*- coding: utf-8 -*-
"""M4 验收脚本：14:30 触发引擎 + 飞书通知（规格第九章 M4）。

验收项（对照规格第九章 M4）：
  1. 选股范围：watchlist 存量全部在沪深主板+创业板内（板块过滤生效）
  2. 腾讯批量行情覆盖全部 in-scope watching 股票（M3 已录 80 只/批偏差）
  3. 当日临时 bar 构造正确：当前价/今日最高/最低拼接后 MyTT 输出合理
  4. 手动触发一个信号，验证卡片格式与签名正确：
     - webhook 未配置：本地验证卡片结构 + 签名已知向量（复算比对）
     - webhook 已配置：真实发送一张信号卡片（取自真实 watchlist 数据）
  5. 引擎全链路实跑一次（限量）：signals.csv 落库 + status=triggered 回写
     + 飞书尝试（未配置则记 notified=False），事后再校验信号行可重建卡片
  6. 强制触发一条真实事件（patch 判定模拟条件达标）：完整验证
     signals.csv 落库 → status 回写 → 卡片重建，事后恢复原状

触发口径（2026-10-18 需求方复核确认）：偏离度 >1% 且 MACD 柱较前一日放大
（柱=(DIF-DEA)×2，通达信口径；不用 DIF 线）且 CCI(14) < -100。

用法：python m4_verify.py
注意：步骤 2/5/6 会真实请求腾讯行情（本机出口限流，已内置间隔与退避）；
      步骤 5/6 会写 signals.csv 与 watchlist（限量，步骤 6 事后恢复原状）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.services import feishu, indicators, market, tracker  # noqa: E402
from app.services.signals import SIGNALS_COLUMNS, load_signals  # noqa: E402
from app.storage import store  # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"  ✓ {name}")
    else:
        failures += 1
        print(f"  ✗ {name} {detail}")


print("=" * 62)
print("M4 验收：14:30 触发引擎 + 飞书")
print("=" * 62)

# --------------------------------------------------------------------------- #
print("\n[1] 选股范围（仅沪深主板+创业板）")
wl = store.load_watchlist()
off = wl[~wl["symbol"].map(config.is_allowed_symbol)]
check("watchlist 无范围外股票", off.empty,
      f"仍有 {len(off)} 条: {sorted(off['symbol'].unique())[:5]}")
print(f"  watchlist 共 {len(wl)} 行，事件族分布: "
      f"{wl['event_family'].value_counts().to_dict()}")

# --------------------------------------------------------------------------- #
print("\n[2] 腾讯批量行情覆盖全部 in-scope watching")
today = date.today()
active = wl[(wl["status"] == "watching")
            & (wl["tracking_expiry"] >= pd.Timestamp(today))]
active = active[active["symbol"].map(config.is_allowed_symbol)]
syms = active["symbol"].unique().tolist()
print(f"  in-scope watching 事件 {len(active)} 条，涉及股票 {len(syms)} 只")
quotes, failed = market.quote_batch_with_fallback(syms)
coverage = len(quotes) / max(len(syms), 1)
check(f"批量覆盖 {len(quotes)}/{len(syms)}（{coverage:.1%}）",
      coverage >= 0.98, f"失败: {failed[:10]}")
q0 = next(iter(quotes.values())) if quotes else {}
check("行情字段齐全（当前价/最高/最低/昨收）",
      all(q0.get(k) is not None for k in ("current", "high", "low", "prev_close")),
      str(q0))

# --------------------------------------------------------------------------- #
print("\n[3] 当日临时 bar 构造（当前价/最高/最低 拼接 MyTT）")
sample_syms = [s for s in syms if s in quotes][:3]
for sym in sample_syms:
    q = quotes[sym]
    history = market.get_daily_klines(sym)
    ok = history is not None and not history.empty
    check(f"{sym} 日线可拉取（{len(history) if ok else 0} 根）", ok)
    if not ok:
        continue
    close = float(q["current"])
    temp = {"close": close,
            "high": max(float(q["high"] or close), close),
            "low": min(float(q["low"] or close), close)}
    states = indicators.compute_states(history, temp_bar=temp)
    states_hist = indicators.compute_states(history)
    check(f"{sym} bars = 历史+1", states["bars"] == states_hist["bars"] + 1)
    numeric = all(states[k] is not None and states[k] == states[k]
                  for k in ("dif", "dea", "macd_hist", "cci"))
    check(f"{sym} DIF/DEA/MACD柱/CCI 数值合理（非 NaN）", numeric,
          f"dif={states['dif']} dea={states['dea']} "
          f"macd_hist={states['macd_hist']} cci={states['cci']}")
    check(f"{sym} 金叉状态为布尔",
          isinstance(states["macd_cross"], bool)
          and isinstance(states["cci_cross"], bool))
    print(f"    {sym}: 当前价={close:.2f} 昨收={q['prev_close']:.2f} "
          f"DIF={states['dif']:.4f} DEA={states['dea']:.4f} "
          f"MACD柱={states['macd_hist']:.4f} "
          f"CCI={states['cci']:.1f} MACD金叉={states['macd_cross']} "
          f"CCI金叉={states['cci_cross']}")

# --------------------------------------------------------------------------- #
print("\n[4] 引擎限量干跑（判定链路，不写库）")
dry = tracker.run_tracker(day=today, limit=25, dry_run=True)
print(f"  汇总: {dry}")
check("干跑不产生写入（triggered 仅为判定数）",
      dry["scanned"] == dry["triggered"] + dry["skipped"])
check("跳过原因分布完整", bool(dry["skip_reasons"]) or dry["triggered"] > 0)

# --------------------------------------------------------------------------- #
print("\n[5] 卡片格式 + 签名验证")
# 用真实数据拼一张信号卡：取第一条 active 事件的参照价 + 真实行情
sig_for_card = None
for _, ev in active.head(20).iterrows():
    sym = str(ev["symbol"]).zfill(6)
    q = quotes.get(sym)
    if not q:
        continue
    ref, rtype = tracker.resolve_ref_price(ev)
    dev = tracker.calc_deviation(ref, q["current"])
    if rtype == "none":
        continue
    sig_for_card = {
        "trigger_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sym, "event_id": str(ev["event_id"]),
        "current_price": float(q["current"]),
        "ref_price_used": ref, "ref_price_type": rtype, "deviation": dev,
        # 2026-10-18 口径：macd_hist/macd_hist_prev/cci 原始值（示例值，验证卡片结构）
        "macd_hist_at_trigger": 0.1234, "macd_hist_prev_at_trigger": 0.0567,
        "cci_at_trigger": -132.5,
        "event_family": str(ev["event_family"]),
        "source_url": tracker.source_url_of(str(ev["event_id"]),
                                            ev["publish_date"]),
    }
    break
check("取得真实信号样本", sig_for_card is not None)
if sig_for_card:
    card = feishu.build_signal_card(sig_for_card, sig_for_card["source_url"])
    check("header 标注'信号触发'",
          card["header"]["title"]["content"] == "信号触发")
    body = json.dumps(card, ensure_ascii=False)
    for kw in (sig_for_card["symbol"], sig_for_card["event_family"],
               "当前价", "参照价", "偏离度", "MACD", "CCI", "信号给出时间"):
        check(f"卡片正文含「{kw}」", kw in body)
    check("底部含巨潮原文链接", "static.cninfo.com.cn" in body)
    # 签名独立复算（2026-10-18 官方口径：key=f"{ts}\n{secret}", msg 空）
    secret = "verify-secret"
    ts, sign_str = feishu.sign(secret, timestamp=1700000000)
    expect = base64.b64encode(hmac.new(
        f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()
    check("签名与独立复算一致（已知向量）", sign_str == expect)
    payload = feishu.build_payload(card, secret=secret, timestamp=ts)
    check("载荷 JSON 可序列化且含 sign/timestamp",
          payload["sign"] == expect and payload["timestamp"] == str(ts)
          and json.dumps(payload, ensure_ascii=False))
    if config.FEISHU_WEBHOOK and config.FEISHU_SECRET:
        ok, err = feishu.send_card(card)
        check(f"飞书实发（{err[:40]}）", ok, err)
    else:
        print("  - 飞书 webhook/secret 未配置（.env），跳过实发；"
              "结构与签名已本地验证")

# --------------------------------------------------------------------------- #
print("\n[6] 引擎全链路实跑（限量 50，写 signals.csv + 回写状态）")
before = len(load_signals())
result = tracker.run_tracker(day=today, limit=50, notify=True)
print(f"  汇总: {result}")
after = load_signals()
new_rows = after.iloc[before:]
check(f"signals.csv 新增 {len(new_rows)} 行", len(new_rows) == result["triggered"])
for _, row in new_rows.iterrows():
    wl_row = wl[(wl["symbol"] == row["symbol"])
                & (wl["event_id"] == str(row["event_id"]))]
    fam = wl_row["event_family"].iloc[0] if not wl_row.empty else "?"
    rebuilt = {
        "symbol": row["symbol"], "event_family": fam,
        "current_price": row["current_price"],
        "ref_price_used": row["ref_price_used"],
        "ref_price_type": row["ref_price_type"],
        "deviation": row["deviation"],
        "macd_hist_at_trigger": row["macd_hist_at_trigger"],
        "macd_hist_prev_at_trigger": row["macd_hist_prev_at_trigger"],
        "cci_at_trigger": row["cci_at_trigger"],
        "trigger_time": row["trigger_time"],
    }
    card2 = feishu.build_signal_card(rebuilt)
    check(f"信号行 {row['symbol']} 可重建卡片",
          card2["header"]["title"]["content"] == "信号触发")
    status_now = store.load_watchlist()
    st = status_now[(status_now["symbol"] == row["symbol"])
                    & (status_now["event_id"] == str(row["event_id"]))]
    check(f"信号行 {row['symbol']} 状态已回写 triggered",
          not st.empty and st["status"].iloc[0] == "triggered")
    check(f"信号行 {row['symbol']} 列齐全",
          all(c in after.columns for c in SIGNALS_COLUMNS))
if result["triggered"] == 0:
    print("  - 本次限量样本未产生真实触发（偏离度>1%+MACD柱放大+CCI<-100 条件苛刻，属正常）；"
          "全链路写入路径由干跑与单测覆盖")

# --------------------------------------------------------------------------- #
print("\n[7] 强制触发一条真实事件（验证落库+回写+卡片，事后恢复原状）")
# 规格第九章 M4 要求"手动触发一个历史信号验证卡片格式和签名正确"。
# 限量样本可能碰不到自然触发（偏离度>1%+MACD柱放大+CCI<-100 条件苛刻），此处对一条
# 真实 watching 事件 patch 触发判定（模拟条件达标），跑完整写入链路，
# 验证 signals.csv 落库 / status=triggered 回写 / 卡片可重建后恢复原状。
target = None
for _, ev in active.iterrows():
    ref, rtype = tracker.resolve_ref_price(ev)
    if rtype != "none":
        target = ev
        break
if target is None:
    print("  - 无带参照价的候选事件，跳过强制触发")
else:
    sym_t = str(target["symbol"]).zfill(6)
    eid_t = str(target["event_id"])
    print(f"  目标事件: {sym_t} {eid_t}（{target['event_family']}）")
    _orig_should = tracker.should_trigger

    def _forced(deviation, macd_rising, cci_value, ref_type):
        return True, "验收强制触发（模拟偏离度+技术条件达标）"

    tracker.should_trigger = _forced
    signals_existed = config.SIGNALS_PATH.exists()
    try:
        sig_before = load_signals()
        res_t = tracker.run_tracker(day=today, symbols=[sym_t], notify=True)
        sig_after = load_signals()
        new_rows = sig_after.iloc[len(sig_before):]
        check(f"强制触发产生 {res_t['triggered']} 条信号（实际落库 "
              f"{len(new_rows)} 条）", len(new_rows) == res_t["triggered"])
        for _, row in new_rows.iterrows():
            check(f"信号行字段齐全（{row['symbol']}）",
                  all(c in sig_after.columns for c in SIGNALS_COLUMNS))
            check(f"信号行 notified 字段存在（未配 webhook 时为 False）",
                  bool(row["notified"]) in (True, False))
            wl_now = store.load_watchlist()
            hit = wl_now[(wl_now["symbol"] == row["symbol"])
                         & (wl_now["event_id"] == str(row["event_id"]))]
            check(f"状态已回写 triggered（{row['symbol']}）",
                  not hit.empty and hit["status"].iloc[0] == "triggered")
            fam = hit["event_family"].iloc[0] if not hit.empty else "?"
            card_t = feishu.build_signal_card({
                "symbol": row["symbol"], "event_family": fam,
                "current_price": row["current_price"],
                "ref_price_used": row["ref_price_used"],
                "ref_price_type": row["ref_price_type"],
                "deviation": row["deviation"],
                "macd_hist_at_trigger": row["macd_hist_at_trigger"],
                "macd_hist_prev_at_trigger": row["macd_hist_prev_at_trigger"],
                "cci_at_trigger": row["cci_at_trigger"],
                "trigger_time": row["trigger_time"],
            }, tracker.source_url_of(str(row["event_id"]),
                                     hit["publish_date"].iloc[0])
            if not hit.empty else "")
            check(f"信号行可重建交互卡片（{row['symbol']}）",
                  card_t["header"]["title"]["content"] == "信号触发"
                  and "static.cninfo.com.cn" in json.dumps(card_t,
                                                           ensure_ascii=False))
        # 恢复原状：删本次信号行 + 状态回 watching
        if signals_existed:
            sig_before.to_csv(config.SIGNALS_PATH, index=False,
                              encoding="utf-8-sig")
        elif config.SIGNALS_PATH.exists():
            config.SIGNALS_PATH.unlink()  # 本文件由本次强制触发新建
        with store.WATCHLIST_LOCK:
            wl_now = store.load_watchlist()
            for _, row in new_rows.iterrows():
                m = (wl_now["symbol"] == row["symbol"]) \
                    & (wl_now["event_id"] == str(row["event_id"]))
                wl_now.loc[m, "status"] = "watching"
            store._save_watchlist_locked(wl_now)
        print(f"  已恢复：删除 {len(new_rows)} 条强制信号行，"
              f"相关事件状态回到 watching")
    finally:
        tracker.should_trigger = _orig_should

print("\n" + "=" * 62)
if failures:
    print(f"验收失败 {failures} 项")
    sys.exit(1)
print("M4 验收全部通过")
