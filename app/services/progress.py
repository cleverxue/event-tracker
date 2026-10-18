# -*- coding: utf-8 -*-
"""M2：自选股公告进展管线（规格 5.2，21:30 任务的服务层）。

对 watchlist 中活跃记录（watching + triggered，见下方边界说明）逐组处理：
1. 逐只拉取 publish_date 至今的全部公告（announcement.query_stock_announcements，
   巨潮 hisAnnouncement/query，pageSize=30 翻页）
2. 按事件族状态机匹配标题，状态迁移写 timeline（storage.append_timeline）
3. 进展/完成类公告下载正文，抽取累计股数与累计金额，
   avg_price = 累计金额 / 累计股数 回填 watchlist，
   avg_price_source 记该公告 event_id（规格 5.2 的唯一均价实现路径）
4. 终态记录 status → completed / terminated；过期记录 status → expired
5. last_progress_check 更新；顺带清理 90 天前快照

交易日判断由调用方（M3 的 21:30 任务 / run_progress.py）负责，
本服务不重复判断——批量回填需要能跑非交易日。

边界（规格未覆盖，取最简方案，已在交付说明标注）：
- 同一股票同一事件族的多条事件（如一年内两次回购）无法从标题可靠归属到
  具体事件，按"同族统一推进"处理：公告对该族全部 watching 事件生效
  （仍受"公告日期 ≥ 事件公告日"约束），avg_price 取各事件自己扫描到的
  最新进展公告计算值；
- 状态只进不退、终态粘滞（到达终态后不再处理该事件的后续公告）；
- timeline 记录全部命中的家族公告（含状态未变的进展公告），
  state_before == state_after 表示公告未推进状态，便于前端展示公告节奏；
- 增持 failed 在 watchlist 状态上记为 terminated（规格 4.1 无 failed 状态），
  timeline 的 state_after 仍保留 failed 供区分；
- avg_price 在进展类（progress）与完成类（completed）公告上都会尝试回填——
  完成公告的累计总数是最权威的均价来源；
- triggered 记录的后续进展仍跟踪（便于复盘信号事件的演化），
  完成/终止/过期记录不再处理（规格 5.2 字面为 watching，此为最小扩写）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd

from app import config
from app.services import announcement, extract
from app.storage import store

log = logging.getLogger(__name__)

# 事件族状态机（规格 5.2）：数字为推进rank，只进不退；终态 rank 相同且粘滞
_STATE_RANK = {
    "回购": {"planned": 1, "in_progress": 2, "completed": 3, "terminated": 3},
    "增持": {"planned": 1, "in_progress": 2, "completed": 3, "failed": 3},
    "协议转让": {"agreed": 1, "approved": 2, "completed": 3, "terminated": 3},
}
# 分类器 stage → 状态机状态
_STAGE_TO_STATE = {
    "回购": {"initial": "planned", "progress": "in_progress",
             "completed": "completed", "terminated": "terminated"},
    "增持": {"initial": "planned", "progress": "in_progress",
             "completed": "completed", "failed": "failed"},
    # 协议转让：规格 5.2 状态机为 agreed→approved→completed/terminated，
    # 无 in_progress——进展公告（"股份转让进展公告"）不迁移状态，
    # 仅记 timeline 与均价回填（None = 不迁移）
    "协议转让": {"initial": "agreed", "approved": "approved",
                 "progress": None, "completed": "completed",
                 "terminated": "terminated"},
}
_INITIAL_STATE = {"回购": "planned", "增持": "planned", "协议转让": "agreed"}
# 终态 → watchlist status（规格 4.1 无 failed，增持失败记 terminated）
_TERMINAL_STATUS = {
    ("回购", "completed"): "completed",
    ("回购", "terminated"): "terminated",
    ("增持", "completed"): "completed",
    ("增持", "failed"): "terminated",
    ("协议转让", "completed"): "completed",
    ("协议转让", "terminated"): "terminated",
}
# 需要下载正文抽均价的阶段（initial 的正文 M1 已抽过价格；approved 无累计量）
_DOWNLOAD_STAGES = ("progress", "completed")

# avg_price 合理性区间（元/股）：超出视为抽取错误，不回填
_AVG_PRICE_MIN, _AVG_PRICE_MAX = 0.01, 100000.0

AUDIT_PATH = config.DATA_DIR / "m2_progress_audit.csv"


# --------------------------------------------------------------------------- #
# 选股（批量回填 / 验证用）
# --------------------------------------------------------------------------- #
def select_stocks(df: pd.DataFrame, limit: int | None,
                  families=None) -> list[tuple[str, str]]:
    """按 (symbol, family) 组挑选 limit 只股票（组），按公告日从早到晚。

    家族间轮询取样，保证 回购/增持/协议转让 都有样本；
    families 给定则只在这些族内选。
    """
    active = df[df["status"].isin(["watching", "triggered"])]
    if families:
        active = active[active["event_family"].isin(list(families))]
    if active.empty or not limit:
        groups = (active[["symbol", "event_family"]]
                  .drop_duplicates().itertuples(index=False))
        return [(r.symbol, r.event_family) for r in groups]

    by_family: dict[str, list[str]] = {}
    for fam, sub in active.groupby("event_family"):
        syms = (sub.sort_values(["publish_date", "symbol"])
                    .drop_duplicates(subset=["symbol"])["symbol"].tolist())
        by_family[fam] = syms
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    # 轮询：每族取一个未选过的股票，直到凑满 limit
    idx = 0
    while len(picked) < limit:
        progressed = False
        for fam in sorted(by_family):
            syms = by_family[fam]
            if idx < len(syms) and syms[idx] not in seen:
                seen.add(syms[idx])
                picked.append((syms[idx], fam))
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break
        idx += 1
    return picked


# --------------------------------------------------------------------------- #
# 单组（symbol + family）处理
# --------------------------------------------------------------------------- #
def _matched_announcements(anns: list[dict], family: str,
                           since: str) -> list[dict]:
    """筛出属于本事件族、且不早于 since（YYYYMMDD）的公告，附分类结果。"""
    out = []
    for a in anns:
        if a["ann_date"] < since:
            continue
        fam, stage, rule = extract.classify_detail(a["title"])
        if fam != family or not stage:
            continue
        a = dict(a, stage=stage, rule=rule)
        out.append(a)
    return out


def _process_event(event: pd.Series, anns: list[dict],
                   texts: dict[str, str], today: date,
                   audit_rows: list[dict]) -> dict:
    """对单个 watchlist 事件跑状态机，返回该事件的字段更新。

    anns 已按 (ann_date, publish_ts) 正序、且属于同一事件族。
    """
    symbol, event_id = event["symbol"], event["event_id"]
    family = event["event_family"]
    pub_ymd = event["publish_date"].strftime("%Y%m%d")
    # 秒级精度：watchlist 该列为 datetime64[ms]，微秒值会触发 dtype 强转失败
    updates: dict = {"last_progress_check": datetime.now().replace(microsecond=0)}

    # --- 种子：初始公告本身 → planned/agreed ---
    seed = next((a for a in anns if a["disclosure_id"] == event_id), None)
    state = _INITIAL_STATE[family]
    seed_row = {
        "event_id": event_id,
        "announcement_id": event_id,
        "publish_timestamp": seed["publish_ts"] if seed
                            else event["publish_date"].strftime("%Y-%m-%d 00:00"),
        "title": seed["title"] if seed else "（初始公告，M1 入库）",
        "matched_rule": "初始公告",
        "state_before": "",
        "state_after": state,
        "source_url": seed["source_url"] if seed else "",
    }
    timeline_rows = [seed_row]
    audit_rows.append({
        "symbol": symbol, "event_id": event_id, "announcement_id": event_id,
        "ann_date": pub_ymd, "publish_ts": seed_row["publish_timestamp"],
        "title": seed_row["title"], "stage": "initial", "matched_rule": "初始公告",
        "state_before": "", "state_after": state,
        "shares": None, "amount": None, "avg_price": None,
        "source_url": seed_row["source_url"],
    })

    # --- 逐条公告推进 ---
    # 只认不早于本事件公告日的公告：同族多事件时（如一年内两次回购），
    # 早于本事件发布日的公告属于其他事件，不得混入（实测 1224728746
    # 曾混入 1224719027 的 2025-10-18 预案， timeline 出现倒序行）
    for a in anns:
        if a["ann_date"] < pub_ymd:
            continue
        if a["disclosure_id"] == event_id:
            continue  # 种子已记录
        stage = a["stage"]
        target = _STAGE_TO_STATE[family][stage]
        if target is None:
            # 协议转让进展公告：状态机无对应状态，保持当前状态，
            # 仅记 timeline（state_before == state_after）与均价回填
            target = state
        if _STATE_RANK[family][target] < _STATE_RANK[family][state]:
            log.debug("%s/%s %s 状态 %s 不进反退，跳过: %s",
                      symbol, event_id, stage, state, a["title"][:30])
            continue

        # 均价回填：进展/完成类公告抽累计股数+金额
        shares = amount = avg = None
        if stage in _DOWNLOAD_STAGES:
            body = texts.get(a["disclosure_id"], "")
            shares, amount = extract.extract_progress(body)
            if shares and amount and shares > 0:
                avg = amount / shares
                if not (_AVG_PRICE_MIN <= avg <= _AVG_PRICE_MAX):
                    log.warning("%s/%s %s 均价 %.2f 超出合理区间，丢弃: %s",
                                symbol, event_id, a["disclosure_id"], avg,
                                a["title"][:30])
                    avg = None

        timeline_rows.append({
            "event_id": event_id,
            "announcement_id": a["disclosure_id"],
            "publish_timestamp": a["publish_ts"],
            "title": a["title"],
            "matched_rule": a["rule"],
            "state_before": state,
            "state_after": target,
            "source_url": a["source_url"],
        })
        audit_rows.append({
            "symbol": symbol, "event_id": event_id,
            "announcement_id": a["disclosure_id"], "ann_date": a["ann_date"],
            "publish_ts": a["publish_ts"], "title": a["title"],
            "stage": stage, "matched_rule": a["rule"],
            "state_before": state, "state_after": target,
            "shares": shares, "amount": amount, "avg_price": avg,
            "source_url": a["source_url"],
        })
        if avg is not None:
            updates["avg_price"] = avg
            updates["avg_price_source"] = a["disclosure_id"]
        state = target
        if (family, stage) in _TERMINAL_STATUS:
            updates["status"] = _TERMINAL_STATUS[(family, stage)]
            break  # 终态粘滞：不再处理该事件后续公告

    # --- 过期 ---
    if "status" not in updates and event["tracking_expiry"].date() < today:
        updates["status"] = "expired"
        log.info("%s/%s 跟踪窗口已过（%s），标记 expired",
                 symbol, event_id, event["tracking_expiry"].date())

    updates["_timeline_rows"] = timeline_rows
    return updates


# --------------------------------------------------------------------------- #
# 主管线
# --------------------------------------------------------------------------- #
def run_progress(symbols=None, families=None, limit: int | None = None,
                 today: date | None = None) -> dict:
    """执行一次进展分析，返回汇总统计。

    symbols: 只跑这些股票；families: 只跑这些事件族；
    limit: 最多跑 N 只（symbol+family 组），按公告日从早到晚、族间轮询；
    today: 默认取当天日期（过期判定用）。
    """
    today = today or date.today()
    wl = store.load_watchlist()
    if wl.empty:
        log.warning("watchlist 为空，无进展可分析")
        return {"groups": 0, "events": 0, "avg_filled": 0,
                "status_changed": 0, "timeline_rows": 0}

    active = wl[wl["status"].isin(["watching", "triggered"])].copy()
    if symbols:
        active = active[active["symbol"].isin([str(s) for s in symbols])]
    if families:
        active = active[active["event_family"].isin(list(families))]

    groups = select_stocks(active, limit, families)
    log.info("本次进展分析: %d 个 (symbol, family) 组", len(groups))

    all_audit: list[dict] = []
    timeline_new = 0
    events_done = 0
    avg_filled = 0
    status_changed = 0
    skipped_groups = 0

    for symbol, family in groups:
        ev_df = active[(active["symbol"] == symbol)
                       & (active["event_family"] == family)]
        if ev_df.empty:
            continue
        start = ev_df["publish_date"].min().strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        try:
            anns = announcement.query_stock_announcements(symbol, start, end)
        except Exception as exc:  # noqa: BLE001 - 单股票失败降级不阻塞
            log.warning("逐只查询失败 %s，跳过该组: %s", symbol, exc)
            skipped_groups += 1
            continue

        matched = _matched_announcements(anns, family, start)
        need_dl = [a for a in matched if a["stage"] in _DOWNLOAD_STAGES]
        texts = (announcement.download_texts(need_dl) if need_dl else {})
        log.info("%s [%s]: 公告 %d 条，本族命中 %d 条（下载正文 %d 篇）",
                 symbol, family, len(anns), len(matched), len(need_dl))

        for _, event in ev_df.sort_values("publish_date").iterrows():
            updates = _process_event(event, matched, texts, today, all_audit)
            events_done += 1
            if "avg_price" in updates:
                avg_filled += 1
            if "status" in updates:
                status_changed += 1
            mask = (wl["symbol"] == event["symbol"]) & (wl["event_id"] == event["event_id"])
            for col in ("avg_price", "avg_price_source", "status",
                        "last_progress_check"):
                if col in updates:
                    wl.loc[mask, col] = updates[col]
            timeline_new += store.append_timeline(updates.pop("_timeline_rows"))

    # 落盘（原子 + 锁）
    if all_audit:
        pd.DataFrame(all_audit).to_csv(AUDIT_PATH, index=False,
                                       encoding="utf-8-sig")
    store.save_watchlist(wl)
    removed = cleanup_snapshots()

    summary = {"groups": len(groups), "events": events_done,
               "avg_filled": avg_filled, "status_changed": status_changed,
               "timeline_rows": timeline_new, "skipped_groups": skipped_groups,
               "snapshots_removed": removed}
    log.info("进展分析完成: %s", summary)
    return summary


def cleanup_snapshots(keep_days: int | None = None) -> int:
    """清理 keep_days 天前的快照（规格 4.4：保留 90 天，21:30 顺带清理）。"""
    keep = keep_days if keep_days is not None else config.SNAPSHOT_KEEP_DAYS
    cutoff = datetime.now() - timedelta(days=keep)
    removed = 0
    for p in config.SNAPSHOT_DIR.glob("watchlist_*.parquet"):
        try:
            d = datetime.strptime(p.stem.replace("watchlist_", ""), "%Y%m%d")
        except ValueError:
            d = datetime.fromtimestamp(p.stat().st_mtime)
        if d < cutoff:
            p.unlink()
            removed += 1
    if removed:
        log.info("清理 %d 天前快照 %d 份", keep, removed)
    return removed
