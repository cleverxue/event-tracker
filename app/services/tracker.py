# -*- coding: utf-8 -*-
"""14:30 触发引擎（规格 5.3，M4 实现本体；M3 为行情层连通性占位）。

流程（规格 5.3，勿简化）：
  1. 过滤 status=watching 且 tracking_expiry >= 今日，且仅沪深主板+创业板
     （板块约束为需求方 2026-10-17 新增，见 config.is_allowed_symbol；
      入库侧已过滤，此处为兜底）
  2. 腾讯批量实时行情（fields[3] 当前价 / [33] 今日最高 / [34] 今日最低），
     失败降级 mootdx 逐只，仍失败当日跳过该股并记日志，不发错误信号
  3. 一级判定（便宜，全量）：参照价优先级 avg_price > price_fixed > range 中值；
     偏离度 = (ref_price - current_price) / current_price
     - ceiling 型：偏离度仅作辅助展示，不门控（规格原文）
     - none 型且无均价回填：无参照价，跳过（宁缺毋滥）
  4. 二级判定（贵，仅幸存者）：拉日线与当日临时 bar（close=当前价、
     high=今日最高、low=今日最低）拼接送 MyTT；技术条件为
     （需求方 2026-10-18 修订，替代原"MACD 且 CCI 双金叉"；
      当日复核确认①用 MACD 柱、不用 DIF 线）：
       ① MACD 柱较前一日放大：柱（=(DIF-DEA)×2，通达信口径）当日值
          > 前一日值（口径为 MACD 柱；若需改回 DIF 线动
          indicators.is_macd_hist_rising 一处）
       ② CCI 低于 -100：CCI(14) 即时值 < config.CCI_OVERSOLD
     两项均基于拼接序列最后一点（当日 14:30 瞬时状态）
  5. 触发：偏离度 > 1% 且技术条件满足（ceiling 型仅技术条件）→ 回写
     status=triggered、追加 signals.csv（macd_hist/macd_hist_prev/cci
     原始值，供人工复核）、发飞书交互卡片（第六章）
  6. 汇总日志：扫描数 / 触发数 / 跳过数 / 日线限流跳过数

性能取向：偏离度门控在前、日线拉取在后——触发是"且"关系，一级未过的
股票拉日线无意义；本机出口对行情源短时限流（越打越封），日线逐只拉取
间强制间隔，失败不硬重试。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import pandas as pd

from app import config
from app.services import feishu, indicators, market, signals
from app.storage import store

log = logging.getLogger(__name__)

# 逐只日线拉取间隔（秒）：避开腾讯短时限流
# 逐只日线拉取间隔（秒）：避开腾讯短期限流。
# 2026-10-18 实测：0.4s 间隔连续约 190 次后触发腾讯 WAF（501 风控页，
# 越打越封），故提到 1.0s 对半砍请求速率；仍可能触顶，靠 market 的
# WAF 识别优雅降级（限流跳过记入汇总，事后按 runbook 补跑）。
_KLINE_INTERVAL = 1.0

# 巨潮原文直链形态（M1 实测）：finalpage/公告日/announcementId.PDF
CNINFO_PDF_BASE = "http://static.cninfo.com.cn/finalpage"


def source_url_of(event_id: str, publish_date) -> str:
    """事件 → 巨潮原文直链（卡片底部附注用）。"""
    pub = pd.Timestamp(publish_date)
    return f"{CNINFO_PDF_BASE}/{pub.strftime('%Y-%m-%d')}/{event_id}.PDF"


# --------------------------------------------------------------------------- #
# 纯判定函数（单测直接覆盖）
# --------------------------------------------------------------------------- #
def resolve_ref_price(row) -> tuple[float | None, str]:
    """参照价优先级（规格 5.3）：avg_price（非空）> price_fixed > range 中值。

    ceiling 型返回 (上限价, "ceiling")——仅作展示不门控；
    none 型且无均价回填返回 (None, "none")——无法判定偏离度，调用方跳过。
    """
    avg = row["avg_price"]
    if pd.notna(avg) and float(avg) > 0:
        return float(avg), "avg"
    ptype = row["price_type"]
    if ptype == "fixed" and pd.notna(row["price_fixed"]):
        return float(row["price_fixed"]), "fixed"
    if ptype == "range" and pd.notna(row["price_low"]) and pd.notna(row["price_high"]):
        return (float(row["price_low"]) + float(row["price_high"])) / 2, "range"
    if ptype == "ceiling" and pd.notna(row["price_high"]):
        return float(row["price_high"]), "ceiling"
    return None, "none"


def calc_deviation(ref_price: float | None, current_price: float | None) -> float | None:
    """偏离度 = (参照价 - 当前价) / 当前价。正数=现价低于参照价（折价）。"""
    if ref_price is None or current_price is None or current_price <= 0:
        return None
    return (ref_price - current_price) / current_price


def should_trigger(deviation: float | None, macd_rising: bool, cci_value,
                   ref_type: str) -> tuple[bool, str]:
    """触发判定（2026-10-18 修订；①口径为 MACD 柱）。返回 (是否触发, 原因)。

    技术条件：MACD 柱较前一日放大 且 CCI < -100。
    ceiling 型：技术条件满足即触发，偏离度仅展示（规格原文平移）。
    其余型：偏离度 > 1% 且技术条件满足。
    """
    if not macd_rising:
        return False, "MACD柱未较前一日放大"
    if cci_value is None:
        return False, "CCI数据不足"
    if float(cci_value) >= config.CCI_OVERSOLD:
        return False, f"CCI {float(cci_value):.1f} 未低于{config.CCI_OVERSOLD}"
    if ref_type == "ceiling":
        return True, "ceiling型技术条件满足即触发（偏离度仅展示）"
    if deviation is None:
        return False, "无参照价（none 型且无均价回填）"
    if deviation > config.DEVIATION_THRESHOLD:
        return True, (f"偏离度 {deviation:.2%} > 1% 且"
                      f"MACD柱放大+CCI<{config.CCI_OVERSOLD}")
    return False, f"偏离度 {deviation:.2%} 未达 1%"


# --------------------------------------------------------------------------- #
# 引擎主体
# --------------------------------------------------------------------------- #
def run_tracker(day: date | None = None, force: bool = False,
                symbols: list[str] | str | None = None,
                limit: int | None = None, dry_run: bool = False,
                notify: bool = True) -> dict:
    """执行一次 14:30 触发引擎，返回汇总。

    day:      判定用日期（默认今天；过期比较与快照口径）
    force:    兼容 jobs 层签名（交易日门禁在 jobs/CLI，引擎内不判）
    symbols:  只跑指定股票（可传单个代码或列表）
    limit:    最多评估 N 条事件（调试抽样）
    dry_run:  干跑——完整判定但完全不写库、不推送
    notify:   是否发飞书（False 时 signals.notified 记 False）
    """
    day = day or date.today()
    today_ts = pd.Timestamp(day)

    # 1. 候选事件：watching 且未过期 且在选股范围内
    wl = store.load_watchlist()
    if wl.empty:
        log.info("watchlist 为空，触发引擎跳过")
        return {"scanned": 0, "triggered": 0, "skipped": 0}
    active = wl[(wl["status"] == "watching")
                & (wl["tracking_expiry"] >= today_ts)].copy()
    board_mask = active["symbol"].map(config.is_allowed_symbol)
    n_offboard = int((~board_mask).sum())
    if n_offboard:
        log.info("板块兜底过滤：剔除 %d 条范围外事件（入库侧应已过滤）", n_offboard)
    active = active[board_mask]
    if symbols:
        wanted = {str(s).split(".")[0].zfill(6)
                  for s in ([symbols] if isinstance(symbols, str) else symbols)}
        active = active[active["symbol"].isin(wanted)]
    active = active.sort_values(["symbol", "publish_date"])
    if limit:
        active = active.head(limit)

    scanned = len(active)
    log.info("14:30 触发引擎: %s 候选事件 %d 条（watching 且未过期且范围内）",
             day, scanned)
    if not scanned:
        return {"scanned": 0, "triggered": 0, "skipped": 0}
    market.reset_kline_rate_limited()

    # 2. 批量行情（腾讯 → mootdx 降级）
    uniq_symbols = active["symbol"].unique().tolist()
    quotes, quote_failed = market.quote_batch_with_fallback(uniq_symbols)

    # 3/4. 逐事件两阶段判定
    skip_reasons: dict[str, int] = {}
    triggered: list[tuple[pd.Series, dict]] = []
    klines_cache: dict[str, pd.DataFrame | None] = {}
    kline_fetches = 0

    def _skip(reason: str) -> None:
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for _, ev in active.iterrows():
        sym = str(ev["symbol"]).zfill(6)
        quote = quotes.get(sym)
        if not quote or quote.get("current") is None:
            _skip("行情缺失")
            continue

        # 一级：偏离度（便宜）
        ref_price, ref_type = resolve_ref_price(ev)
        deviation = calc_deviation(ref_price, quote["current"])
        if ref_type == "none":
            _skip("无参照价")
            continue
        need_cross = (ref_type == "ceiling") or (
            deviation is not None and deviation > config.DEVIATION_THRESHOLD)
        if not need_cross:
            _skip("偏离度未达1%")
            continue

        # 二级：即时金叉（贵，按 symbol 缓存日线）
        if sym not in klines_cache:
            if kline_fetches:
                time.sleep(_KLINE_INTERVAL)  # 限流间隔，见模块 docstring
            klines_cache[sym] = market.get_daily_klines(sym)
            kline_fetches += 1
        history = klines_cache[sym]
        if history is None or history.empty:
            _skip("日线缺失")
            continue

        # 当日临时 bar：当前价/今日最高/今日最低（防御性保证 high≥close≥low）
        close = float(quote["current"])
        high = max(float(quote["high"] or close), close)
        low = min(float(quote["low"] or close), close)
        states = indicators.compute_states(
            history, temp_bar={"close": close, "high": high, "low": low})
        if states["macd_hist"] is None or states["cci"] is None:
            _skip("指标数据不足")
            continue

        macd_rising = indicators.is_macd_hist_rising(
            states["macd_hist"], states["macd_hist_prev"])
        ok, reason = should_trigger(deviation, macd_rising,
                                    states["cci"], ref_type)
        if not ok:
            _skip(reason)
            continue

        signal = {
            "trigger_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym,
            "event_id": str(ev["event_id"]),
            "current_price": close,
            "ref_price_used": ref_price,
            "ref_price_type": ref_type,
            "deviation": deviation,
            # 触发时技术条件原始值（人工复核用，2026-10-18 口径；
            # ①改为 MACD 柱后记录柱值与柱前值）
            "macd_hist_at_trigger": states["macd_hist"],
            "macd_hist_prev_at_trigger": states["macd_hist_prev"],
            "cci_at_trigger": states["cci"],
            # 以下字段仅入 signals.csv/卡片，不入 watchlist
            "event_family": str(ev["event_family"]),
            "source_url": source_url_of(str(ev["event_id"]), ev["publish_date"]),
        }
        triggered.append((ev, signal))
        log.info("触发: %s %s 当前价=%.2f 参照价=%.2f(%s) 偏离度=%s "
                 "MACD柱=%.4f(前值%.4f) CCI=%.1f 原因=%s",
                 sym, signal["event_id"], close, ref_price or 0, ref_type,
                 f"{deviation:.2%}" if deviation is not None else "-",
                 states["macd_hist"],
                 states["macd_hist_prev"] or float("nan"),
                 states["cci"], reason)

    # 5. 触发处理：飞书 → signals.csv → watchlist 状态回写
    notified_ok = 0
    if triggered:
        if dry_run:
            log.info("dry-run：以下 %d 条将触发（不写库不推送）", len(triggered))
            for _, sig in triggered:
                log.info("  [dry-run] %s %s 当前价=%.2f 偏离度=%s",
                         sig["symbol"], sig["event_id"], sig["current_price"],
                         f"{sig['deviation']:.2%}" if sig["deviation"] is not None else "-")
        else:
            for _, sig in triggered:
                ok, err = (feishu.send_signal_card(sig, sig["source_url"])
                           if notify else (False, "notify=False 跳过推送"))
                if ok:
                    notified_ok += 1
                sig["notified"] = ok
                sig["notified_time"] = (datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S") if ok else "")
                signals.append_signal(sig)
            # watchlist 状态回写：单次加锁读-改-写（防与手动触发接口并发）
            with store.WATCHLIST_LOCK:
                wl_now = store.load_watchlist()
                for _, sig in triggered:
                    mask = (wl_now["symbol"] == sig["symbol"]) \
                        & (wl_now["event_id"] == sig["event_id"])
                    wl_now.loc[mask, "status"] = "triggered"
                store._save_watchlist_locked(wl_now)
            log.info("已回写 %d 条事件 status=triggered", len(triggered))

    summary = {
        "scanned": scanned,
        "triggered": len(triggered),
        "skipped": scanned - len(triggered),
        "skip_reasons": skip_reasons,
        "quotes_covered": len(quotes),
        "quotes_failed": len(quote_failed),
        "kline_fetches": kline_fetches,
        "kline_rate_limited": len(market.kline_rate_limited()),
        "notified": notified_ok,
        "dry_run": dry_run,
    }
    log.info("14:30 触发引擎汇总: 扫描 %d / 触发 %d / 跳过 %d（%s）"
             "日线限流跳过 %d 只",
             scanned, len(triggered), scanned - len(triggered), skip_reasons,
             len(market.kline_rate_limited()))
    return summary
