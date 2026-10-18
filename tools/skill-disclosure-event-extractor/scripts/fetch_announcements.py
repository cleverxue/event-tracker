#!/usr/bin/env python3
"""Fetch A-share announcement lists and full texts from cninfo (巨潮).

Legally-public disclosures only. Respects rate limits, caches originals for
reproducibility, and records source_url / disclosure_id for traceability.

Usage:
    python fetch_announcements.py --symbols 000001.SZ 600000.SH \
        --start 20260101 --end 20260630 --out cache/

Dependencies: requests, pdfplumber (optional: pytesseract for scanned PDFs).
This is the L2-runnable data-acquisition layer; event extraction happens
downstream against event-taxonomy.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable

import requests

CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH = "http://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_ORGID = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_STATIC = "http://static.cninfo.com.cn/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (quantskills disclosure-event-extractor)",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
}

MIN_INTERVAL = 1.0  # seconds between requests (rate-limit courtesy)
MAX_RETRIES = 5


@dataclass
class Announcement:
    symbol: str
    sec_name: str
    ann_date: str          # YYYYMMDD
    title: str
    source_url: str
    disclosure_id: str
    fetched_at: str        # ISO8601


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _polite_post(url: str, data: dict, _last=[0.0]) -> requests.Response:
    """POST with fixed min-interval + exponential backoff on 429/5xx."""
    for attempt in range(MAX_RETRIES):
        wait = MIN_INTERVAL - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        resp = requests.post(url, data=data, headers=HEADERS, timeout=20)
        _last[0] = time.monotonic()
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(2 ** attempt, 30))
            continue
        resp.raise_for_status()
    raise RuntimeError(f"cninfo request failed after {MAX_RETRIES} retries: {url}")


def load_orgid_map(cache_dir: str) -> dict[str, str]:
    """code (6-digit) -> orgId, cached for a day."""
    path = os.path.join(cache_dir, "orgid_map.json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path) < 86400):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    resp = requests.get(CNINFO_ORGID, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    mapping = {row["code"]: row["orgId"] for row in resp.json().get("stockList", [])}
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False)
    return mapping


def query_list(symbol: str, start: str, end: str, orgid_map: dict[str, str]
               ) -> list[Announcement]:
    """Query the full announcement list for one symbol in [start, end]."""
    code = symbol.split(".")[0]
    org_id = orgid_map.get(code, "")
    se_date = f"{_fmt(start)}~{_fmt(end)}"
    out: list[Announcement] = []
    page = 1
    while True:
        data = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": "30",
            "pageNum": str(page),
            # NB: cninfo's `column` filter (szse/sse) is over-restrictive and
            # returns 0 rows for valid stock+date queries; omitting it lets the
            # backend infer the market from `stock`. Verified empirically.
            "seDate": se_date,
            "isHLtitle": "true",
        }
        payload = _polite_post(CNINFO_QUERY, data).json()
        anns = payload.get("announcements") or []
        for a in anns:
            ts = int(a["announcementTime"]) / 1000
            out.append(Announcement(
                symbol=symbol,
                sec_name=a.get("secName", ""),
                ann_date=datetime.fromtimestamp(ts).strftime("%Y%m%d"),
                title=a.get("announcementTitle", ""),
                source_url=CNINFO_STATIC + a.get("adjunctUrl", ""),
                disclosure_id=str(a.get("announcementId", "")),
                fetched_at=_iso_now(),
            ))
        if not payload.get("hasMore"):
            break
        page += 1
    return out


_EM_RE = re.compile(r"</?em>")


def _suffix_for(code: str) -> str:
    """Infer exchange suffix from a 6-digit code."""
    if code.startswith("6"):
        return "SH"
    if code[0] in ("4", "8") or code.startswith("92"):
        return "BJ"
    return "SZ"


def search_list(keyword: str, start: str, end: str, max_pages: int = 20
                ) -> list[Announcement]:
    """Full-text keyword search ACROSS the whole market (Tier-A discovery).

    Unlike query_list (per-symbol), this finds events by keyword when you don't
    know which stocks disclosed them — e.g. all 关注函 / 诉讼 in a window.
    """
    out: list[Announcement] = []
    page = 1
    while page <= max_pages:
        data = {
            "searchkey": keyword,
            "sdate": _fmt(start), "edate": _fmt(end),
            "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
            "pageNum": str(page), "pageSize": "30",
        }
        payload = _polite_post(CNINFO_SEARCH, data).json()
        anns = payload.get("announcements") or []
        for a in anns:
            ts = int(a["announcementTime"]) / 1000
            code = a.get("secCode", "")
            out.append(Announcement(
                symbol=f"{code}.{_suffix_for(code)}",
                sec_name=a.get("secName", ""),
                ann_date=datetime.fromtimestamp(ts).strftime("%Y%m%d"),
                title=_EM_RE.sub("", a.get("announcementTitle", "")),
                source_url=CNINFO_STATIC + a.get("adjunctUrl", ""),
                disclosure_id=str(a.get("announcementId", "")),
                fetched_at=_iso_now(),
            ))
        if not payload.get("hasMore"):
            break
        page += 1
    return out


def download_text(ann: Announcement, cache_dir: str) -> str:
    """Download the original PDF and extract its text layer; cache by id."""
    raw_path = os.path.join(cache_dir, f"{ann.disclosure_id}.pdf")
    txt_path = os.path.join(cache_dir, f"{ann.disclosure_id}.txt")
    if os.path.exists(txt_path):
        with open(txt_path, encoding="utf-8") as fh:
            return fh.read()
    if not os.path.exists(raw_path):
        resp = requests.get(ann.source_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        with open(raw_path, "wb") as fh:
            fh.write(resp.content)
    text = _pdf_to_text(raw_path)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def _pdf_to_text(path: str) -> str:
    try:
        import pdfplumber  # lazy import so listing works without the dep
    except ImportError:
        raise SystemExit("pip install pdfplumber to extract announcement text")
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    text = "\n".join(parts).strip()
    # Scanned PDFs have no text layer — OCR fallback is optional and left to
    # the caller (pytesseract) to avoid a heavy hard dependency.
    return text


def _fmt(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _collect(anns: list[Announcement], out_dir: str, manifest: list[dict],
             seen: set, no_text: bool = False) -> None:
    for ann in anns:
        if ann.disclosure_id in seen:
            continue
        seen.add(ann.disclosure_id)
        if not no_text:  # title-only mode skips PDF download (large scans)
            try:
                download_text(ann, out_dir)
            except Exception as exc:  # noqa: BLE001 - record and continue
                print(f"[warn] {ann.disclosure_id} download failed: {exc}")
        manifest.append(asdict(ann))


def run(start: str, end: str, out_dir: str,
        symbols: Iterable[str] = (), keywords: Iterable[str] = (),
        no_text: bool = False) -> str:
    os.makedirs(out_dir, exist_ok=True)
    manifest: list[dict] = []
    no_disclosure: list[str] = []
    seen: set = set()

    if symbols:
        orgid_map = load_orgid_map(out_dir)
        for sym in symbols:
            anns = query_list(sym, start, end, orgid_map)
            if not anns:
                no_disclosure.append(sym)
            _collect(anns, out_dir, manifest, seen, no_text)

    for kw in keywords:
        anns = search_list(kw, start, end)
        print(f"[search] '{kw}': {len(anns)} announcements")
        _collect(anns, out_dir, manifest, seen, no_text)

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"announcements": manifest, "no_disclosure": no_disclosure},
                  fh, ensure_ascii=False, indent=2)
    print(f"[ok] {len(manifest)} announcements, "
          f"{len(no_disclosure)} symbols with no disclosure -> {manifest_path}")
    return manifest_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=[],
                    help="per-symbol query, e.g. 000001.SZ 600000.SH")
    ap.add_argument("--keywords", nargs="+", default=[],
                    help="full-text search across market, e.g. 关注函 诉讼")
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--out", default="cache", help="cache / output dir")
    ap.add_argument("--no-text", action="store_true",
                    help="titles only: skip PDF download (large-scale scans)")
    args = ap.parse_args()
    if not args.symbols and not args.keywords:
        ap.error("provide --symbols and/or --keywords")
    run(args.start, args.end, args.out,
        symbols=args.symbols, keywords=args.keywords, no_text=args.no_text)


if __name__ == "__main__":
    main()
