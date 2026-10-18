# -*- coding: utf-8 -*-
"""公告拉取层（M1/M2 共用）。

封装 tools/skill-disclosure-event-extractor/scripts/fetch_announcements.py：
1. 关键词全市场搜索：巨潮 fulltextSearch/full（标题驱动），pageSize=100，
   自适应时间分块绕开深翻页上限；三关键词分开跑，按 disclosure_id 合并去重。
2. 原文下载：static.cninfo.com.cn PDF 直链 + pdfplumber 抽文本，落 cache/，
   并发 2 + 每请求 ≥1s 间隔（遵守源站约定），指数退避，缓存幂等。
3. 逐只查询：hisAnnouncement/query（pageSize=30 翻页），供 21:30 进展拉取（M2）。
   注：本机实测该接口逐只查询可用；"全市场列表筛选"失效的结论不适用于逐只模式。
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from app import config

log = logging.getLogger(__name__)

TOOLS_DIR = config.PROJECT_ROOT / "tools" / "skill-disclosure-event-extractor" / "scripts"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import fetch_announcements as skill_fa  # noqa: E402  （skill 的拉取实现，GPL-3.0，见 LICENSE）

PAGE_SIZE = 100           # 实测 fulltextSearch 支持 100/页
MAX_PAGES = 50            # 单时间块最多翻 50 页（5000 条），到顶继续二分
CHUNK_MAX_RECORDS = 1500  # 单块总量超过此值先二分时间窗
DOWNLOAD_WORKERS = 2      # 源站约定：单会话并发 ≤2

_EM_RE = re.compile(r"</?em>")

# A 股代码规则（6 位）：沪主板 60xxxx、科创 688xxx、深主板 000xxx-003xxx、
# 创业 300xxx/301xxx、北交所 8xxxxx/4xxxxx/92xxxx。
# cninfo 全文检索会返回港股公告（5 位代码，如 06936 顺丰、01618 中冶）——
# A+H 公司的港股通道公告是其 A 股通道公告的重复（A 股回购按规定须在沪深
# 交易所披露），按代码形态剔除，避免同一事件双行入库。
_A_SHARE_RE = re.compile(r"^(?:60\d{4}|68\d{4}|00\d{4}|30\d{4}|8\d{5}|4\d{5}|92\d{4})$")


def is_a_share_symbol(code: str) -> bool:
    """是否 A 股 6 位代码（港股 5 位代码返回 False）。"""
    return bool(_A_SHARE_RE.match(code or ""))


class _Throttle:
    """线程安全的最小间隔节流器。"""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.interval:
                time.sleep(self.interval - gap)
            self._last = time.monotonic()


_search_throttle = _Throttle(1.0)
_download_throttle = _Throttle(1.0)


# --------------------------------------------------------------------------- #
# 关键词全市场搜索
# --------------------------------------------------------------------------- #
def _fmt(yyyymmdd: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD（fulltextSearch 要求带横线）。"""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _search_page(keyword: str, sdate: str, edate: str, page_num: int) -> dict:
    data = {
        "searchkey": keyword,
        "sdate": _fmt(sdate), "edate": _fmt(edate),
        "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
        "pageNum": str(page_num), "pageSize": str(PAGE_SIZE),
    }
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            _search_throttle.wait()
            resp = requests.post(skill_fa.CNINFO_SEARCH, data=data,
                                 headers=skill_fa.HEADERS, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            last_err = exc
        time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"fulltextSearch 失败（{keyword} {sdate}~{edate} p{page_num}）: {last_err}")


def _normalize_search_item(a: dict) -> dict:
    ts = int(a["announcementTime"]) / 1000
    code = a.get("secCode") or ""
    return {
        "disclosure_id": str(a.get("announcementId", "")),
        "symbol": code,  # 纯 6 位代码，联合主键用
        "sec_name": a.get("secName", ""),
        "ann_date": datetime.fromtimestamp(ts).strftime("%Y%m%d"),
        "publish_ts": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
        "title": _EM_RE.sub("", a.get("announcementTitle", "")),
        "source_url": skill_fa.CNINFO_STATIC + a.get("adjunctUrl", ""),
    }


def _search_range(keyword: str, start: str, end: str,
                  out: list[dict], seen: set, depth: int = 0) -> None:
    """单关键词单时间块搜索；总量过大则二分时间窗递归。"""
    first = _search_page(keyword, start, end, 1)
    total = int(first.get("totalRecordNum") or 0)
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if total > CHUNK_MAX_RECORDS and span > 1 and depth < 6:
        mid = date.fromisoformat(start) + timedelta(days=span // 2)
        left_end = (mid - timedelta(days=1)).strftime("%Y%m%d")
        right_start = mid.strftime("%Y%m%d")
        log.info("  %s %s~%s 共 %d 条，二分", keyword, start, end, total)
        _search_range(keyword, start, left_end, out, seen, depth + 1)
        _search_range(keyword, right_start, end, out, seen, depth + 1)
        return

    page = 1
    while page <= MAX_PAGES:
        payload = first if page == 1 else _search_page(keyword, start, end, page)
        anns = payload.get("announcements") or []
        for a in anns:
            item = _normalize_search_item(a)
            if not item["disclosure_id"] or not is_a_share_symbol(item["symbol"]):
                continue  # 无溯源 ID 或港股代码，剔除
            if item["disclosure_id"] not in seen:
                seen.add(item["disclosure_id"])
                out.append(item)
        if not anns or not payload.get("hasMore"):
            break
        page += 1
    else:
        log.warning("%s %s~%s 翻页到达 MAX_PAGES=%d，可能有遗漏",
                    keyword, start, end, MAX_PAGES)


def search_keyword(keyword: str, start: str, end: str) -> list[dict]:
    """单关键词全市场搜索，YYYYMMDD，返回归一化公告清单。"""
    out: list[dict] = []
    seen: set = set()
    _search_range(keyword, start, end, out, seen)
    log.info("关键词[%s] %s~%s: %d 条", keyword, start, end, len(out))
    return out


def search_families(start: str, end: str,
                    families=config.EVENT_FAMILIES) -> list[dict]:
    """三关键词分开拉取 + 按 disclosure_id 合并去重（规格 5.1 第 2 步）。"""
    merged: dict[str, dict] = {}
    for fam in families:
        for item in search_keyword(fam, start, end):
            key = item["disclosure_id"]
            if key in merged:
                merged[key].setdefault("matched_keywords", []).append(fam)
            else:
                item["matched_keywords"] = [fam]
                merged[key] = item
    log.info("三关键词合并去重: %d 条（%s~%s）", len(merged), start, end)
    return list(merged.values())


# --------------------------------------------------------------------------- #
# 原文下载 + 文本抽取
# --------------------------------------------------------------------------- #
def _download_one(rec: dict, cache_dir: Path) -> str:
    """下载单篇原文并抽文本；命中缓存直接读。失败返回空串（不抛断主流程）。"""
    did = rec["disclosure_id"]
    txt_path = cache_dir / f"{did}.txt"
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8")
    pdf_path = cache_dir / f"{did}.pdf"
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            _download_throttle.wait()
            resp = requests.get(rec["source_url"], headers=skill_fa.HEADERS, timeout=30)
            resp.raise_for_status()
            pdf_path.write_bytes(resp.content)
            text = skill_fa._pdf_to_text(pdf_path)
            txt_path.write_text(text, encoding="utf-8")
            return text
        except Exception as exc:  # noqa: BLE001 - 单篇失败不阻塞整批
            last_err = exc
            time.sleep(min(2 ** attempt, 20))
    log.warning("原文下载失败 %s（%s）: %s", did, rec.get("title", "")[:30], last_err)
    return ""


def download_texts(records: list[dict], cache_dir: Path | None = None,
                   workers: int = DOWNLOAD_WORKERS) -> dict[str, str]:
    """并发下载原文文本（缓存幂等，可重复跑断点续传）。"""
    cache_dir = Path(cache_dir or config.CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in records if r.get("source_url")]
    results: dict[str, str] = {}
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_one, r, cache_dir): r for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = futures[fut]
            try:
                text = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("下载任务异常 %s: %s", rec["disclosure_id"], exc)
                text = ""
            results[rec["disclosure_id"]] = text
            if text:
                ok += 1
            else:
                failed += 1
            if i % 100 == 0:
                log.info("原文下载进度 %d/%d（成功 %d 失败 %d）", i, len(todo), ok, failed)
    log.info("原文下载完成: 成功 %d, 失败 %d", ok, failed)
    return results


# --------------------------------------------------------------------------- #
# 逐只公告查询（M2 21:30 进展拉取用）
# --------------------------------------------------------------------------- #
# 巨潮 hisAnnouncement/query，pageSize=30 翻页（规格 5.2）。
# 关键约定（与 skill 的 query_list 一致，本机实测）：
#   - stock 参数为 "代码,orgId"，column 过滤对合法股票+日期查询会返回 0 行，省略
#   - seDate 为 "YYYY-MM-DD~YYYY-MM-DD"，起止含当天
#   - 本机实测该接口逐只查询可用；"全市场列表筛选失效"的结论不适用于逐只模式
_query_throttle = _Throttle(1.0)
QUERY_PAGE_SIZE = 30
QUERY_MAX_PAGES = 200   # 单股票最多 6000 条，防呆


def _query_stock_page(code: str, org_id: str, sdate: str, edate: str,
                      page_num: int) -> dict:
    data = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(QUERY_PAGE_SIZE),
        "pageNum": str(page_num),
        "seDate": f"{_fmt(sdate)}~{_fmt(edate)}",
        "isHLtitle": "true",
    }
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            _query_throttle.wait()
            resp = requests.post(skill_fa.CNINFO_QUERY, data=data,
                                 headers=skill_fa.HEADERS, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            last_err = exc
        time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"hisAnnouncement/query 失败（{code} p{page_num}）: {last_err}")


def query_stock_announcements(symbol: str, start: str, end: str) -> list[dict]:
    """逐只查询 [start, end] 全部公告，按时间正序返回。

    symbol 支持 '600519' 或 '600519.SH'；起止为 YYYYMMDD（含当天）。
    返回字段含 publish_ts（精确到时分，timeline 溯源用）；查询失败抛错，
    由调用方降级（记日志、跳过该股票，不阻塞主流程）。
    """
    code = symbol.split(".")[0]
    orgid_map = skill_fa.load_orgid_map(str(config.CACHE_DIR))
    org_id = orgid_map.get(code, "")
    if not org_id:
        log.warning("orgid 映射缺失 %s，跳过该股票（可删除 %s 后重试刷新映射）",
                    code, config.CACHE_DIR / "orgid_map.json")
        return []

    out: list[dict] = []
    page = 1
    while page <= QUERY_MAX_PAGES:
        payload = _query_stock_page(code, org_id, start, end, page)
        anns = payload.get("announcements") or []
        for a in anns:
            ts = int(a["announcementTime"]) / 1000
            out.append({
                "disclosure_id": str(a.get("announcementId", "")),
                "symbol": code,
                "sec_name": a.get("secName", ""),
                "ann_date": datetime.fromtimestamp(ts).strftime("%Y%m%d"),
                "publish_ts": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                "title": _EM_RE.sub("", a.get("announcementTitle", "")),
                "source_url": skill_fa.CNINFO_STATIC + a.get("adjunctUrl", ""),
            })
        if not anns or not payload.get("hasMore"):
            break
        page += 1
    else:
        log.warning("%s 逐只查询到达 QUERY_MAX_PAGES=%d，可能有遗漏",
                    code, QUERY_MAX_PAGES)
    out.sort(key=lambda r: (r["ann_date"], r["publish_ts"]))
    log.info("逐只查询 %s %s~%s: %d 条", code, start, end, len(out))
    return out
