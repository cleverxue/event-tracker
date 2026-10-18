#!/usr/bin/env python3
"""Event-driven backtest: do extracted events precede the returns their
direction implies? Produces the evidence needed to move this skill L2 -> L3.

For each event row it aligns the announcement date to the first trading day
(t0) and measures forward returns at T+1 / T+5 / T+10 / T+20, then aggregates
by `direction` and by `event_type`:
  - mean / median forward return per horizon
  - hit-rate: share of 利好 events that rose, share of 利空 events that fell
  - the 利好 − 利空 spread (a positive spread at a horizon = the labels carry
    real forward information)

Prices come from eastmoney's free daily kline endpoint (front-adjusted, no auth).
This is evidence, not a trading strategy: no costs, no position sizing, and the
sample is whatever event table you feed it. Report the sample size with results.

Usage:
    python backtest_events.py /tmp/tierA_events.csv --out /tmp/bt_detail.csv
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta

import requests

SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData")
HEADERS = {"User-Agent": "Mozilla/5.0 (quantskills disclosure-event-extractor)",
           "Referer": "https://finance.sina.com.cn"}
HORIZONS = [1, 5, 10, 20]
MIN_INTERVAL = 0.4


def _sina_symbol(symbol: str) -> str:
    code, _, ex = symbol.partition(".")
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(ex.upper(), "sh") + code


def _polite_get(url: str, params: dict, _last=[0.0]) -> requests.Response:
    wait = MIN_INTERVAL - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    _last[0] = time.monotonic()
    r.raise_for_status()
    return r


def fetch_daily(symbol: str, beg: str, end: str) -> tuple[list[str], list[float]]:
    """Return (dates 'YYYYMMDD', unadjusted close) from `beg` onward.

    Sina returns the most recent `datalen` daily bars (no date range), so size
    the window to reach `beg` and trim. Prices are unadjusted — fine for short
    T+1..T+20 horizons; a dividend inside the window adds minor noise.
    """
    today = datetime.now()
    back_days = (today - datetime.strptime(beg, "%Y%m%d")).days
    datalen = max(60, min(1023, int(back_days * 5 / 7) + 40))
    params = {"symbol": _sina_symbol(symbol), "scale": "240",
              "ma": "no", "datalen": str(datalen)}
    arr = json.loads(_polite_get(SINA_KLINE, params).text.strip() or "[]")
    dates, closes = [], []
    for bar in arr:
        d = bar["day"].replace("-", "")[:8]
        if d >= beg:
            dates.append(d)
            closes.append(float(bar["close"]))
    return dates, closes


def forward_returns(dates: list[str], closes: list[float], ann_date: str
                    ) -> dict[int, float]:
    """Forward return at each horizon from t0 = first trading day >= ann_date."""
    t0 = next((i for i, d in enumerate(dates) if d >= ann_date), None)
    out: dict[int, float] = {}
    if t0 is None or closes[t0] == 0:
        return out
    base = closes[t0]
    for h in HORIZONS:
        j = t0 + h
        if j < len(closes):
            out[h] = closes[j] / base - 1.0
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _median(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def run(path: str, out_path: str | None, benchmark: str) -> None:
    import pandas as pd
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path, dtype={"ann_date": str})
    df = df[df["direction"].isin(["利好", "利空", "中性"])].copy()

    lo_all = min(df["ann_date"])

    # benchmark once, to neutralize market beta (excess returns are the real
    # evidence — raw returns are dominated by whatever the index did).
    try:
        b_dts, b_cls = fetch_daily(benchmark, lo_all, "")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] benchmark fetch failed {benchmark}: {exc}")
        b_dts, b_cls = ([], [])

    prices: dict[str, tuple] = {}
    for sym, g in df.groupby("symbol"):
        try:
            prices[sym] = fetch_daily(sym, min(g["ann_date"]), "")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] price fetch failed {sym}: {exc}")
            prices[sym] = ([], [])

    rows = []
    for _, e in df.iterrows():
        dts, cls = prices.get(e["symbol"], ([], []))
        fr = forward_returns(dts, cls, e["ann_date"])
        fb = forward_returns(b_dts, b_cls, e["ann_date"])
        rec = {"symbol": e["symbol"], "ann_date": e["ann_date"],
               "event_type": e["event_type"], "direction": e["direction"],
               "severity": e.get("severity", "")}
        for h in HORIZONS:
            rec[f"ret_t{h}"] = fr.get(h)
            # excess = stock − benchmark over the same horizon
            rec[f"exret_t{h}"] = (fr[h] - fb[h]) if (h in fr and h in fb) else None
        rows.append(rec)
    bt = pd.DataFrame(rows)

    if out_path:
        bt.to_csv(out_path, index=False)

    _report(bt, benchmark)


def _report(bt, benchmark: str) -> None:
    def agg(sub, col) -> str:
        cells = []
        for h in HORIZONS:
            xs = [x for x in sub[f"{col}_t{h}"].tolist() if x == x]  # drop NaN
            cells.append(f"T+{h}:{_mean(xs)*100:+5.2f}%(med {_median(xs)*100:+5.2f})")
        return "  ".join(cells)

    print(f"\n=== Event-driven EXCESS returns vs {benchmark} (n={len(bt)} events) ===")
    print("市场中性化后（超额收益 = 个股 − 基准）——这才是事件信号，原始收益被大盘 beta 主导。")
    print("Direction breakdown (excess):")
    for d in ["利好", "利空", "中性"]:
        sub = bt[bt["direction"] == d]
        if len(sub):
            print(f"  {d} (n={len(sub):>3}): {agg(sub, 'exret')}")

    has_up = (bt["direction"] == "利好").any()
    has_dn = (bt["direction"] == "利空").any()
    if has_up or has_dn:
        print("\nSignal check on EXCESS (does the label predict market-relative direction?):")
        for h in HORIZONS:
            up = [x for x in bt[bt.direction == "利好"][f"exret_t{h}"].tolist() if x == x]
            dn = [x for x in bt[bt.direction == "利空"][f"exret_t{h}"].tolist() if x == x]
            parts = []
            if up:
                parts.append(f"利好跑赢占比 {_mean([x > 0 for x in up])*100:5.1f}%")
            if dn:
                parts.append(f"利空跑输占比 {_mean([x < 0 for x in dn])*100:5.1f}%")
            if up and dn:
                parts.append(f"利好−利空超额价差 {(_mean(up)-_mean(dn))*100:+5.2f}%")
            print(f"  T+{h:>2}: " + " | ".join(parts))

    print("\nBy event_type (mean excess return):")
    for et in sorted(bt["event_type"].unique()):
        sub = bt[bt["event_type"] == et]
        print(f"  {et:>22} (n={len(sub):>3}): {agg(sub, 'exret')}")

    cov = bt["exret_t20"].notna().mean() * 100 if len(bt) else 0.0
    print(f"\n[note] 原始收益（未中性化）仅供参考，已一并写入 --out CSV 的 ret_t* 列。")
    print(f"[note] 样本 n={len(bt)}，T+20 有效样本覆盖 {cov:.1f}%（其余因停牌/退市/新上市无足够行情被剔除）。")
    print(f"[note] 价格为不复权：短周期内除权除息引入少量噪声；退市股被剔除会带来幸存者偏差。")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("events", help="event table from extract_events.py (.csv/.parquet)")
    ap.add_argument("--out", help="optional per-event forward-return CSV")
    ap.add_argument("--benchmark", default="000300.SH",
                    help="market benchmark for excess returns (default CSI300)")
    args = ap.parse_args()
    run(args.events, args.out, args.benchmark)


if __name__ == "__main__":
    main()
