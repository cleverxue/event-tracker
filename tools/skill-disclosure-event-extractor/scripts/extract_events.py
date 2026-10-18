#!/usr/bin/env python3
"""Step 4: turn cached announcement text into a structured event table.

Reads the manifest + cached .txt files produced by fetch_announcements.py,
classifies each announcement by the deterministic rules in
references/event-taxonomy.md, extracts fields (subject / magnitude / ratio /
period) via regex, maps direction & severity, merges/dedups, and writes an
event table that conforms to references/output-schema.md.

The LLM fill step is OPTIONAL and pluggable: with no LLM wired in, the script
runs fully deterministic (rules-only) so it stays runnable offline. Wire
`llm_fill` to Claude (see the stub) to backfill fields the rules leave blank.

Usage:
    python extract_events.py --cache cache/ --out reports/events/20260630.csv
    python extract_events.py --cache cache/ --out reports/events/20260630.parquet
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Taxonomy — ordered; first matching type wins. Mirrors event-taxonomy.md.
# --------------------------------------------------------------------------- #
# Regulatory / litigation checked early: their titles are distinctive and must
# not be shadowed by generic governance keywords.
TAXONOMY: list[tuple[str, list[str]]] = [
    ("regulatory_inquiry", ["问询函", "关注函", "监管函", "警示函", "立案", "处罚"]),
    ("litigation_guarantee", ["诉讼", "仲裁", "对外担保", "涉案", "被告", "原告"]),
    # suspension 早于 earnings/holding/restructuring：控制权变更/要约收购/易主往往
    # 伴随停牌，且比"收购"(重组)、"协议转让"(增减持)语义更高一级，须优先归此类。
    ("suspension", ["停牌", "复牌", "控制权变更", "实际控制人变更", "控股股东变更",
                    "易主", "要约收购", "入主"]),
    ("earnings_preview", ["业绩预告", "预增", "预减", "扭亏", "首亏", "业绩预告修正"]),
    ("pledge", ["股权质押", "质押", "解除质押", "补充质押", "平仓"]),
    ("holding_change", ["增持", "减持", "协议转让", "被动稀释"]),
    ("restructuring", ["重大资产重组", "发行股份购买资产", "收购", "重大合同", "中标"]),
    ("governance", ["股东大会决议", "董事会决议", "换届", "辞职", "选举",
                    "利润分配", "权益分派", "回购", "高送转", "分红"]),
]

DIRECTIONS = {"利好", "利空", "中性"}
SEVERITIES = {"高", "中", "低"}

# Tier A (primary) = Pandadata has NO structured feed -> this skill's core value.
# Tier B (supplement) = Pandadata already structures it (event-risk-alert
# consumes those feeds); we only capture the text-level detail Pandadata drops.
TIER = {
    "regulatory_inquiry": "primary",
    "litigation_guarantee": "primary",
    "suspension": "primary",
    "restructuring": "primary",
    "governance": "primary",
    "holding_change": "supplement",
    "pledge": "supplement",
    "earnings_preview": "supplement",
}

# Text-level nuances Pandadata's structured feeds don't carry. Populated into
# `detail` for Tier B events (the reason those supplement rows are worth keeping).
_DETAIL_HINTS = [
    "平仓线", "警戒线", "补充质押", "质押用途", "偿还债务", "个人资金需求", "资金需求",
    "减持原因", "价格区间", "集中竞价", "大宗交易",
    "业绩预告修正", "前次预告", "较上年同期",
]

# --------------------------------------------------------------------------- #
# Field extraction helpers
# --------------------------------------------------------------------------- #
_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# Amounts MUST tolerate thousands separators: A-share filings overwhelmingly
# write 「担保金额人民币50,000万元」, not 「50000万元」. A bare (\d+(\.\d+)?) makes
# re.search latch onto the digits AFTER the last comma, which does not fail --
# it silently returns a wrong number: "50,000万元" -> 0.0 (not None, so the
# caller cannot tell it apart from a real zero) and "12,345.67万元" -> 345.67万,
# exactly 100x low. Both then propagate into the severity grade.
# The leading (?<![\d.]) stops a partial match inside a number the pattern
# cannot fully consume, so a malformed amount yields None rather than a
# plausible-looking fragment.
#
# Bare 「元」 is deliberately NOT matched: it appears in 每股收益/面值/股价 contexts
# far more often than in deal sizes, and matching it would trade a silent
# wrong-number bug for a silent false-positive one.
_AMT_NUM = r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_AMT_YI_RE = re.compile(_AMT_NUM + r"\s*亿元")
_AMT_WAN_RE = re.compile(_AMT_NUM + r"\s*万元")
# Curated, specific actors only. Bare "股东"/"董事"/"监事" are excluded on
# purpose: they appear in nearly every governance filing and yield a
# meaningless, non-distinguishing subject.
_SUBJECT_HINTS = ["控股股东", "实际控制人", "实控人", "第一大股东",
                  "高级管理人员", "高管", "上交所", "深交所", "证监会"]


def extract_ratio(text: str) -> Optional[float]:
    m = _RATIO_RE.search(text)
    return round(float(m.group(1)) / 100.0, 6) if m else None


def _to_number(raw: str) -> Optional[float]:
    """Parse a captured amount, dropping thousands separators. None if unusable."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def extract_amount(text: str) -> Optional[float]:
    """Return an amount in yuan, or None. Never returns a partially-parsed number.

    亿元 is checked before 万元 because the 万元 pattern would otherwise never see
    a 亿元 figure anyway, and the ordering documents the unit precedence.
    """
    for pattern, scale in ((_AMT_YI_RE, 1e8), (_AMT_WAN_RE, 1e4)):
        m = pattern.search(text)
        if m:
            value = _to_number(m.group(1))
            if value is not None:
                return value * scale
    return None


def extract_subject(title: str, body: str) -> str:
    for hint in _SUBJECT_HINTS:
        if hint in title:
            return hint
    for hint in _SUBJECT_HINTS:
        if hint in body[:500]:
            return hint
    return ""


def extract_detail(title: str, body: str) -> str:
    """Text-level nuance Pandadata's structured feed lacks (Tier B value)."""
    hay = title + body[:1500]
    hits = [h for h in _DETAIL_HINTS if h in hay]
    return ";".join(dict.fromkeys(hits))  # de-dup, preserve order


def extract_period(text: str) -> str:
    m = re.search(r"(20\d{2})\s*年?\s*(半年度|上半年|一季度|三季度|年度|中期)", text)
    if m:
        table = {"半年度": "H1", "上半年": "H1", "中期": "H1",
                 "一季度": "Q1", "三季度": "Q3", "年度": "FY"}
        return f"{m.group(1)}{table.get(m.group(2), '')}"
    return ""


# --------------------------------------------------------------------------- #
# Classification + subtype + direction/severity (rules from event-taxonomy.md)
# --------------------------------------------------------------------------- #
def classify(title: str, body: str) -> Optional[str]:
    """Classify on the TITLE only.

    A-share disclosure titles are standardized and precise; the PDF body is
    noisy (a 关联交易公告 mentions 收购, a 半年报 mentions 分红/减持). Matching
    the body yields heavy false positives, so classification is title-driven.
    Generic titles (董事会决议/关联交易公告) that carry a real event only in the
    body are intentionally left to the optional --llm pass rather than guessed.
    Trades recall for precision — a data-supply layer must not poison downstream
    factors with false events.
    """
    # Policy/rule documents (e.g. "对外担保管理制度") mention event keywords but
    # are NOT events — drop them before classification to avoid false positives.
    if any(n in title for n in ("管理制度", "管理办法", "制度", "内部控制")):
        return None
    for etype, kws in TAXONOMY:
        if any(k in title for k in kws):
            return etype
    return None


def subtype_and_direction(etype: str, title: str, body: str, subject: str,
                          ratio: Optional[float], amount: Optional[float]
                          ) -> tuple[str, str, str]:
    """Return (event_subtype, direction, severity)."""
    hay = title + body[:800]
    big_subject = any(s in subject for s in ("控股股东", "实际控制人", "实控人", "第一大股东"))

    if etype == "holding_change":
        up = "增持" in hay and "减持" not in title
        intent = "拟" in hay or "计划" in hay
        sub = ("增持" if up else "减持") + ("意向" if intent else "结果")
        direction = "利好" if up else "利空"
        sev = "高" if big_subject or (ratio and ratio >= 0.01) else \
              "中" if (ratio and ratio >= 0.005) else "低"
        return sub, direction, sev

    if etype == "pledge":
        if "解除质押" in hay or "解质押" in hay:
            return "解质押", "利好", ("中" if ratio and ratio >= 0.02 else "低")
        if "平仓" in hay:
            return "平仓风险", "利空", "高"
        sub = "补充质押" if "补充质押" in hay else "质押"
        sev = "高" if (ratio and ratio >= 0.05) else "中" if (ratio and ratio >= 0.02) else "低"
        return sub, "利空", sev

    if etype == "earnings_preview":
        if "修正" in hay:
            return "预告修正", "中性", "中"
        if "扭亏" in hay:
            return "扭亏", "利好", "高"
        if "首亏" in hay:
            return "首亏", "利空", "高"
        pos = any(k in hay for k in ("预增", "增长", "增加", "上升", "同向上升"))
        neg = any(k in hay for k in ("预减", "下降", "减少", "亏损", "同向下降"))
        if pos and not neg:
            sub, direction = "预增", "利好"
        elif neg and not pos:
            sub, direction = "预减", "利空"
        else:
            # 方向不明（如仅标题"业绩预告"、正文缺失或增减并存）——不臆测方向
            return "业绩预告", "中性", "低"
        sev = "高" if (ratio and ratio >= 0.5) else "中" if (ratio and ratio >= 0.2) else "低"
        return sub, direction, sev

    if etype == "suspension":
        ctrl = any(k in title for k in ("控制权变更", "实际控制人变更", "控股股东变更",
                                        "易主", "要约收购", "入主"))
        if ctrl:
            # 实测(2022-24, n=605): 中位超额 -1.3%/T+20、跑赢仅44%，均值(+0.6%)全靠尾部
            # 借壳个例拉正——正偏度"彩票"分布，非可靠做多信号。故方向记「中性」、
            # severity「高」(高波动/重大事项)。tail alpha 见 references/l3-evidence.md。
            return "控制权变更", "中性", "高"
        if "复牌" in title and "停牌" not in title:
            return "复牌", "中性", "低"
        # 一般重大事项停牌：方向待定（复牌后才兑现），不臆测
        return "停牌", "中性", "中"

    if etype == "regulatory_inquiry":
        if "立案" in hay:
            return "立案调查", "利空", "高"
        if "处罚" in hay:
            return "行政处罚", "利空", "高"
        sub = "关注函" if "关注函" in hay else "监管函" if ("监管函" in hay or "警示函" in hay) else "问询函"
        return sub, "利空", "中"

    if etype == "litigation_guarantee":
        if "对外担保" in hay or "担保" in title:
            sev = "高" if (amount and amount >= 1e8) else "中"
            return "对外担保", "利空", sev
        sub = "仲裁" if "仲裁" in hay else "诉讼"
        is_plaintiff = "原告" in hay and "被告" not in title
        direction = "中性" if is_plaintiff else "利空"
        sev = "高" if (amount and amount >= 1e8) else "中"
        return sub, direction, sev

    if etype == "restructuring":
        terminated = "终止" in hay or "失败" in hay
        if "重大资产重组" in hay or "发行股份购买资产" in hay:
            sub = "重组进展" if ("进展" in hay or "完成" in hay) else "重组预案"
        elif "中标" in hay or "重大合同" in hay:
            sub = "重大合同"
        else:
            sub = "收购"
        direction = "利空" if terminated else "利好"
        sev = "高" if ("借壳" in hay or (amount and amount >= 1e9)) else "中"
        return sub, direction, sev

    # governance — driven by the (already title-gated) title
    if "回购" in title:
        return "股份回购", "利好", "中"
    if any(k in title for k in ("利润分配", "权益分派", "分红", "高送转")):
        return "利润分配", "利好", "中"
    if ("辞职" in title and ("董事长" in title or "总经理" in title)) or "未获通过" in title or "被否" in title:
        return ("高管变动" if "辞职" in title else "股东大会决议"), "利空", "中"
    sub = "董事会决议" if "董事会决议" in title else "股东大会决议"
    return sub, "中性", "低"


# --------------------------------------------------------------------------- #
# Optional LLM backfill hook (rules-only by default; returns {} => no change)
# --------------------------------------------------------------------------- #
def llm_fill(title: str, body: str, partial: dict) -> dict:
    """Backfill fields the rules left blank. Default: no-op (deterministic).

    To enable, replace the body with a Claude call, e.g.:

        from anthropic import Anthropic
        client = Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=512,
            system="Extract ONLY the missing fields as JSON. No investment advice.",
            messages=[{"role": "user", "content": f"{title}\n\n{body[:4000]}\n\n"
                       f"Missing: {[k for k,v in partial.items() if not v]}"}],
        )
        return json.loads(msg.content[0].text)

    Any field it returns must still pass validate_events.py enum checks; the
    caller re-validates, so a bad LLM value is dropped rather than trusted.
    """
    return {}


# --------------------------------------------------------------------------- #
# Event assembly
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    symbol: str
    sec_name: str
    ann_date: str
    event_type: str
    event_subtype: str
    tier: str
    direction: str
    severity: str
    subject: str = ""
    detail: str = ""
    magnitude: Optional[float] = None
    magnitude_unit: str = ""
    period: str = ""
    summary: str = ""
    stage: str = ""
    timeline: list[str] = field(default_factory=list)
    source_url: str = ""
    disclosure_id: str = ""
    confidence: float = 0.5
    fetched_at: str = ""


def build_event(ann: dict, body: str, use_llm: Callable) -> Optional[Event]:
    title = ann.get("title", "")
    etype = classify(title, body)
    if not etype:
        return None

    ratio = extract_ratio(title) or extract_ratio(body[:1200])
    amount = extract_amount(title) or extract_amount(body[:1200])
    subject = extract_subject(title, body)
    period = extract_period(title) or extract_period(body[:800])

    subtype, direction, severity = subtype_and_direction(
        etype, title, body, subject, ratio, amount)

    tier = TIER[etype]
    # Text-level detail is the reason a Tier B (Pandadata-overlapping) row is
    # worth keeping at all; skip the scan for Tier A to save work.
    detail = extract_detail(title, body) if tier == "supplement" else ""

    magnitude, unit = (None, "")
    if ratio is not None:
        magnitude, unit = ratio, "ratio"
    elif amount is not None:
        magnitude, unit = amount, "amount"

    # confidence: rules hit + key fields present -> 0.9; rules hit, gaps -> 0.7
    rules_complete = bool(subject) and magnitude is not None
    confidence = 0.9 if rules_complete else 0.7

    ev = Event(
        symbol=ann["symbol"], sec_name=ann.get("sec_name", ""),
        ann_date=ann["ann_date"], event_type=etype, event_subtype=subtype,
        tier=tier, direction=direction, severity=severity, subject=subject,
        detail=detail, magnitude=magnitude, magnitude_unit=unit, period=period,
        summary=title[:80], source_url=ann.get("source_url", ""),
        disclosure_id=str(ann.get("disclosure_id", "")),
        confidence=confidence, fetched_at=ann.get("fetched_at", ""),
    )

    # Optional LLM backfill for still-blank fields only
    gaps = {k: getattr(ev, k) for k in ("subject", "period") if not getattr(ev, k)}
    if gaps:
        for k, v in (use_llm(title, body, gaps) or {}).items():
            if hasattr(ev, k) and v and not getattr(ev, k):
                setattr(ev, k, v)
                ev.confidence = min(ev.confidence, 0.7)
    return ev


def merge(events: list[Event]) -> list[Event]:
    """Fuse the SAME sub-event disclosed repeatedly.

    Keyed on (symbol, event_type, event_subtype, subject). Including
    event_subtype is deliberate: without it, unrelated governance items
    (回购 + 利润分配 + 董事会决议) collapse into one row and the survivor's
    fields misrepresent the others. Distinct sub-events must stay distinct for
    a data-supply layer. (A 意向→结果 chain therefore stays as two rows — both
    are real disclosures; downstream can join them on subject if needed.)
    """
    buckets: dict[tuple, list[Event]] = {}
    for ev in events:
        # Periodic decision meetings (每次董事会/股东大会) are distinct events
        # even when identically titled, so never merge them across dates —
        # fold disclosure_id into the key to keep each one.
        distinct = ev.event_subtype in ("董事会决议", "股东大会决议")
        tail = ev.disclosure_id if distinct else ""
        key = (ev.symbol, ev.event_type, ev.event_subtype, ev.subject, tail)
        buckets.setdefault(key, []).append(ev)
    merged: list[Event] = []
    for group in buckets.values():
        group.sort(key=lambda e: e.ann_date)
        head = group[-1]  # latest disclosure carries the row
        head.timeline = [e.disclosure_id for e in group]
        head.stage = "结果" if len(group) > 1 else head.stage
        merged.append(head)
    return merged


def run(cache_dir: str, out_path: str, enable_llm: bool) -> None:
    manifest_path = os.path.join(cache_dir, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    use_llm = llm_fill if enable_llm else (lambda *_: {})
    events: list[Event] = []
    for ann in manifest.get("announcements", []):
        txt_path = os.path.join(cache_dir, f"{ann['disclosure_id']}.txt")
        body = ""
        if os.path.exists(txt_path):
            with open(txt_path, encoding="utf-8") as fh:
                body = fh.read()
        ev = build_event(ann, body, use_llm)
        if ev:
            events.append(ev)

    events = merge(events)
    _write(events, out_path)
    print(f"[ok] {len(events)} events -> {out_path} "
          f"({len(manifest.get('no_disclosure', []))} symbols had no disclosure)")


def _write(events: list[Event], out_path: str) -> None:
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("pip install pandas (and pyarrow for parquet) to write output")
    rows = []
    for ev in events:
        d = asdict(ev)
        d["timeline"] = ";".join(d["timeline"])  # flatten for csv/parquet
        rows.append(d)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if out_path.endswith(".parquet"):
        df.to_parquet(out_path, index=False)
    else:
        df.to_csv(out_path, index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="cache", help="dir with manifest.json + *.txt")
    ap.add_argument("--out", required=True, help="output .csv or .parquet")
    ap.add_argument("--llm", action="store_true", help="enable LLM field backfill")
    args = ap.parse_args()
    run(args.cache, args.out, args.llm)


if __name__ == "__main__":
    main()
