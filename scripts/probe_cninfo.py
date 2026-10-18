# -*- coding: utf-8 -*-
"""M1 探测脚本：验证 cninfo 三个关键能力
1. fulltextSearch/full 关键词搜索：分页上限、pageSize 是否生效、单窗口召回量
2. PDF 下载 + pdfplumber 文本抽取是否可用（watchlist 价格字段依赖正文）
3. hisAnnouncement/query 逐只查询是否可用（M2 进展拉取依赖）
用法：python probe_cninfo.py
"""
import sys
import time
from datetime import datetime

import requests

TOOLS = r"C:/Users/admin/WorkBuddy/2026-10-17-13-58-00/event-tracker/tools/skill-disclosure-event-extractor/scripts"
sys.path.insert(0, TOOLS)
import fetch_announcements as fa  # noqa: E402

PROBE_CACHE = "cache/probe"


def search(keyword, sdate, edate, page_size=30, page_num=1):
    data = {
        "searchkey": keyword,
        "sdate": sdate, "edate": edate,
        "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
        "pageNum": str(page_num), "pageSize": str(page_size),
    }
    return fa._polite_post(fa.CNINFO_SEARCH, data).json()


def main():
    # ---- 1. 关键词搜索：一周窗口 ----
    for ps in (30, 100):
        payload = search("回购", "2026-10-10", "2026-10-16", page_size=ps)
        anns = payload.get("announcements") or []
        print(f"[search] pageSize={ps}: 返回 {len(anns)} 条, "
              f"totalRecordNum={payload.get('totalRecordNum')}, hasMore={payload.get('hasMore')}")
        if ps == 30 and anns:
            a = anns[0]
            print(f"[search] 样本: {a.get('secCode')} {a.get('secName')} "
                  f"{a.get('announcementTitle')} | {a.get('adjunctUrl')}")

    # 一个月窗口看量级（判断是否需要分块）
    payload = search("回购", "2026-09-01", "2026-09-30")
    n = len(payload.get("announcements") or [])
    print(f"[search] 回购 2026-09 单月首页: {n} 条, hasMore={payload.get('hasMore')}")

    # ---- 2. PDF 下载 + 抽文本 ----
    payload = search("回购", "2026-10-10", "2026-10-16")
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
        try:
            text = fa.download_text(ann, PROBE_CACHE)
            print(f"[pdf] {ann.disclosure_id} 下载+抽文本 {time.time()-t0:.1f}s, {len(text)} 字符")
            print("[pdf] 开头片段:", text[:400].replace("\n", " | "))
        except Exception as exc:  # noqa: BLE001
            print(f"[pdf] 失败: {exc}")

    # ---- 3. hisAnnouncement/query 逐只查询 ----
    orgid_map = fa.load_orgid_map(PROBE_CACHE)
    print(f"[orgid] 映射表 {len(orgid_map)} 只, 600519 -> {orgid_map.get('600519')}")
    try:
        anns = fa.query_list("600519.SH", "20260901", "20261016", orgid_map)
        print(f"[query_list] 600519 近 6 周: {len(anns)} 条公告")
        for a in anns[:3]:
            print("  ", a.ann_date, a.title)
    except Exception as exc:  # noqa: BLE001
        print(f"[query_list] 失败: {exc}")


if __name__ == "__main__":
    main()
