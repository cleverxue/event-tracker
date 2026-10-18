# -*- coding: utf-8 -*-
"""M1 探测脚本 第二轮：
1. PDF 下载 + 文本抽取（先建目录，验证价格短语可抽）
2. fulltextSearch 月度量级 + 翻页稳定性
3. hisAnnouncement/query 逐只查询（宽日期窗复测）
4. 东方财富 np-anotice-stock per-stock 对照（M2 备选源）
"""
import sys
import time
from datetime import datetime

sys.path.insert(0, r"C:/Users/admin/WorkBuddy/2026-10-17-13-58-00/event-tracker/tools/skill-disclosure-event-extractor/scripts")
import fetch_announcements as fa  # noqa: E402
import requests  # noqa: E402

PROBE_CACHE = "cache/probe"
import os  # noqa: E402
os.makedirs(PROBE_CACHE, exist_ok=True)


def search(keyword, sdate, edate, page_size=100, page_num=1):
    data = {
        "searchkey": keyword,
        "sdate": sdate, "edate": edate,
        "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
        "pageNum": str(page_num), "pageSize": str(page_size),
    }
    return fa._polite_post(fa.CNINFO_SEARCH, data).json()


def main():
    # ---- 1. PDF 下载 + 抽文本 ----
    payload = search("回购", "2026-10-10", "2026-10-16", page_size=5)
    anns = payload.get("announcements") or []
    if anns:
        a = anns[0]
        ts = int(a["announcementTime"]) / 1000
        ann = fa.Announcement(
            symbol=a.get("secCode", ""), sec_name=a.get("secName", ""),
            ann_date=datetime.fromtimestamp(ts).strftime("%Y%m%d"),
            title=a.get("announcementTitle", ""),
            source_url=fa.CNINFO_STATIC + a.get("adjunctUrl", ""),
            disclosure_id=str(a.get("announcementId", "")), fetched_at="")
        t0 = time.time()
        text = fa.download_text(ann, PROBE_CACHE)
        print(f"[pdf] {ann.title[:40]} | {time.time()-t0:.1f}s, {len(text)} 字符")
        # 找价格相关片段
        import re
        for pat in ("回购价格", "不超过", "不低于", "元/股"):
            idxs = [m.start() for m in re.finditer(re.escape(pat), text)][:2]
            for i in idxs:
                print(f"  [{pat}] ...{text[max(0,i-30):i+60].replace(chr(10),' ')}...")

    # ---- 2. 月度量级 + 翻页 ----
    payload = search("回购", "2026-09-01", "2026-09-30", page_size=100)
    print(f"[search] 回购 2026-09: totalRecordNum={payload.get('totalRecordNum')}")
    p2 = search("回购", "2026-09-01", "2026-09-30", page_size=100, page_num=2)
    a1 = {x.get("announcementId") for x in (payload.get("announcements") or [])}
    a2 = {x.get("announcementId") for x in (p2.get("announcements") or [])}
    print(f"[search] 第2页 {len(a2)} 条, 与第1页重叠 {len(a1 & a2)} 条")
    for kw in ("增持", "协议转让"):
        p = search(kw, "2026-10-10", "2026-10-16", page_size=100)
        print(f"[search] {kw} 一周: totalRecordNum={p.get('totalRecordNum')}")

    # ---- 3. hisAnnouncement/query 逐只复测（宽窗口） ----
    orgid_map = fa.load_orgid_map(PROBE_CACHE)
    try:
        anns = fa.query_list("600519.SH", "20260101", "20261016", orgid_map)
        print(f"[query_list] 600519 2026 年至今: {len(anns)} 条")
        for a in anns[:5]:
            print("  ", a.ann_date, a.title[:40])
    except Exception as exc:  # noqa: BLE001
        print(f"[query_list] 失败: {exc}")

    # ---- 4. 东方财富 per-stock 对照 ----
    try:
        r = requests.get(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"stock_list": "600519", "begin_time": "2026-09-01",
                    "end_time": "2026-10-16", "page_size": 50, "page_index": 1,
                    "ann_type": "A", "sr": -1},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        data = r.json().get("data") or {}
        lst = data.get("list") or []
        print(f"[eastmoney] 600519 per-stock: {len(lst)} 条")
        for item in lst[:5]:
            codes = item.get("codes") or [{}]
            print("  ", item.get("notice_date"), codes[0].get("stock_code"),
                  (item.get("title") or "")[:40])
    except Exception as exc:  # noqa: BLE001
        print(f"[eastmoney] 失败: {exc}")


if __name__ == "__main__":
    main()
