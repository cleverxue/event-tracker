# -*- coding: utf-8 -*-
"""行情层：腾讯 qt.gtimg.cn 实时行情 + 日线（规格第二章）。

字段映射（GBK 编码，~ 分隔，0-based）：
  fields[3]  当前价      fields[4]  昨收
  fields[33] 今日最高    fields[34] 今日最低
  fields[1]  名称        fields[2]  代码

14:30 触发引擎的当日临时 bar 口径（规格第三章/5.3）：
  close = fields[3]（当前价）、high = fields[33]、low = fields[34]

降级链（规格十-6）：腾讯批量失败 → mootdx 逐只 → 当日跳过并记日志。
节流：本机出口网络对行情源有短时限流特性（越打越封），批量请求间
强制间隔，失败指数退避，不可硬重试。
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import requests

from app import config

log = logging.getLogger(__name__)

QT_QUOTE_URL = "https://qt.gtimg.cn/q="
QT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
# 备用日线端点（主端点不可用时降级；2026-10-18 起主端点优先，原因见
# get_daily_klines docstring：fqkline 已被腾讯 WAF 风控频繁 501）
QT_KLINE_URL_FALLBACK = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 单次请求覆盖的股票数上限。规格 5.3 要求"所有 symbol 逗号拼接单次请求"，
# 但腾讯对 q= 参数长度与服务端返回体有上限（实测千只级 URL 被拒），
# 取 80 只/批在稳定与请求数间取平衡；已在交付说明标注。
BATCH_SIZE = 80

# 批量请求间隔（秒）：避开限流
_BATCH_INTERVAL = 1.0
# 失败退避基数（秒）
_BACKOFF_BASE = 1.0
_MAX_RETRIES = 3

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://gu.qq.com/",
}


def to_qt_symbol(symbol: str) -> str:
    """A 股代码 → 腾讯代码。

    600519→sh600519，000001→sz000001；
    北交所 43/83/87/920 开头 → bj（920 是北交所新代码段，
    错配 sh 会被腾讯返回 v_pv_none_match——M3 实测 52 只）。
    """
    code = str(symbol).strip().split(".")[0].zfill(6)
    if code.startswith(("43", "83", "87", "920")):
        return f"bj{code}"
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


class RateLimitedError(RuntimeError):
    """腾讯 WAF 风控（501 页面）——继续重试只会越封越久，必须冷却。"""


def _get_with_backoff(url: str, encoding: str = "gbk",
                      timeout: int = 15) -> requests.Response:
    """带指数退避的 GET（1→2→4s，封顶 8s；3 次后抛异常由降级链处理）。

    WAF 识别：501 或响应体含 waf.tencent.com 时直接抛 RateLimitedError，
    不做无谓重试（实测：撞墙后持续重试会显著延长封禁，静默冷却才恢复）。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 501 or "waf.tencent.com" in resp.text[:500]:
                raise RateLimitedError(
                    f"腾讯 WAF 风控（501），需静默冷却: {url[:80]}")
            resp.raise_for_status()
            resp.encoding = encoding
            return resp
        except RateLimitedError:
            raise  # 风控不重试，直接上传
        except requests.RequestException as exc:
            last_exc = exc
            wait = min(_BACKOFF_BASE * (2 ** attempt), 8.0)
            log.warning("行情请求失败（第 %d/%d 次）%.1fs 后重试: %s",
                        attempt + 1, _MAX_RETRIES, wait, exc)
            time.sleep(wait)
    raise RuntimeError(f"行情请求连续失败: {last_exc}")


# --------------------------------------------------------------------------- #
# 实时行情（14:30 触发引擎用）
# --------------------------------------------------------------------------- #
def _parse_quote_line(line: str) -> dict | None:
    """解析 v_sh600519="..." 单行。"""
    if "=" not in line:
        return None
    payload = line.split("=", 1)[1].strip().strip('"')
    f = payload.split("~")
    if len(f) <= 34:
        return None

    def _num(idx: int) -> float | None:
        try:
            v = float(f[idx])
            return v if v != 0 else None
        except (ValueError, IndexError):
            return None

    return {
        "name": f[1] if len(f) > 1 else "",
        "current": _num(config.QT_FIELDS["current"]),
        "prev_close": _num(config.QT_FIELDS["prev_close"]),
        "high": _num(config.QT_FIELDS["high"]),
        "low": _num(config.QT_FIELDS["low"]),
        "quote_time": f[30] if len(f) > 30 else "",
    }


def get_realtime_quotes(symbols: list[str]) -> dict[str, dict]:
    """腾讯批量实时行情，返回 {symbol: {current, prev_close, high, low, ...}}。

    按 BATCH_SIZE 分批请求；解析失败的股票不在返回字典中（调用方降级）。
    """
    symbols = [str(s).split(".")[0].zfill(6) for s in dict.fromkeys(symbols)]
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        q = ",".join(to_qt_symbol(s) for s in batch)
        try:
            resp = _get_with_backoff(QT_QUOTE_URL + q)
        except RuntimeError as exc:
            log.warning("腾讯批量行情批次 %d 失败: %s", i // BATCH_SIZE, exc)
            time.sleep(_BATCH_INTERVAL)
            continue
        for line in resp.text.strip().split(";"):
            line = line.strip()
            quote = _parse_quote_line(line)
            if not quote or quote.get("current") is None:
                continue
            out[_code_from_line(line)] = quote
        time.sleep(_BATCH_INTERVAL)
    log.info("腾讯批量实时行情: %d/%d 只成功", len(out), len(symbols))
    return out


def _code_from_line(line: str) -> str:
    f = line.split("=", 1)[1].strip().strip('"').split("~")
    return f[2].zfill(6) if len(f) > 2 else ""


def get_realtime_quote_mootdx(symbol: str) -> dict | None:
    """mootdx 降级：逐只查询实时行情。失败返回 None。"""
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        log.warning("mootdx 未安装，降级路径不可用")
        return None
    try:
        client = Quotes.factory(market="std")
        df = client.quotes(symbol=str(symbol).zfill(6))
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "name": str(row.get("name", "")),
            "current": float(row["price"]) if pd.notna(row.get("price")) else None,
            "prev_close": float(row["last_close"]) if pd.notna(row.get("last_close")) else None,
            "high": float(row["high"]) if pd.notna(row.get("high")) else None,
            "low": float(row["low"]) if pd.notna(row.get("low")) else None,
            "quote_time": "",
        }
    except Exception as exc:  # noqa: BLE001 - 降级路径任何失败都只记日志
        log.warning("mootdx 查询 %s 失败: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# 历史日线（指标计算用：与当日临时 bar 拼接）
# --------------------------------------------------------------------------- #
# 日线拉取被腾讯 WAF 限流跳过的 symbol（本轮引擎周期内累计，
# 供 tracker 汇总区分"数据缺失"与"限流跳过"）
_kline_rate_limited: list[str] = []


def reset_kline_rate_limited() -> None:
    """清空限流记录（每轮引擎开始时调用）。"""
    _kline_rate_limited.clear()


def kline_rate_limited() -> list[str]:
    return list(_kline_rate_limited)


def get_daily_klines(symbol: str, count: int = 320) -> pd.DataFrame | None:
    """腾讯日线，返回 DataFrame[date, open, close, high, low, volume]。

    优先不复权（day）：即时行情 fields[3]/[33]/[34] 是不复权价，
    临时 bar 必须与不复权历史拼接，否则除权股指标失真（口径硬要求）。
    不复权不可得时退前复权（qfqday）并在日志标注。

    端点选择（2026-10-18 修正）：主用 kline/kline。原用的 fqkline 端点
    实测在约 190 次连续请求后被腾讯 WAF 风控（501 页面，越打越封），
    kline/kline 返回同构数据且经交叉验证为不复权口径（其收盘与即时
    行情昨收逐一吻合）。fqkline 保留为降级端点。
    """
    qt = to_qt_symbol(symbol)
    for url_base in (QT_KLINE_URL, QT_KLINE_URL_FALLBACK):
        url = f"{url_base}?param={qt},day,,,{count},"
        try:
            resp = _get_with_backoff(url, encoding="utf-8")
            data = resp.json()["data"][qt]
        except RateLimitedError as exc:
            log.warning("日线拉取被限流 %s: %s", symbol, exc)
            _kline_rate_limited.append(str(symbol).zfill(6))
            return None
        except Exception as exc:  # noqa: BLE001 - 单只失败降级不阻塞
            log.warning("日线拉取失败 %s: %s", symbol, exc)
            continue  # 尝试下一个端点
        rows = data.get("day")
        adjusted = False
        if not rows:
            rows = data.get("qfqday")
            adjusted = True
        if not rows:
            continue
        if adjusted:
            log.info("%s 不复权日线不可得，使用前复权（除权股口径可能失真）", symbol)
        df = pd.DataFrame([r[:6] for r in rows],
                          columns=["date", "open", "close", "high", "low", "volume"])
        for col in ("open", "close", "high", "low", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        return df.dropna(subset=["close"]).reset_index(drop=True)
    return None


def quote_batch_with_fallback(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """14:30 引擎取价入口：腾讯批量 → mootdx 逐只降级。

    返回 (quotes, failed)：quotes 为 {symbol: quote}，failed 为彻底失败的
    symbol 列表（调用方对这些当日跳过价格判定并记日志，不发错误信号）。
    """
    quotes = get_realtime_quotes(symbols)
    failed = [s for s in symbols if str(s).zfill(6) not in quotes]
    if failed:
        log.warning("腾讯批量未覆盖 %d 只，降级 mootdx 逐只查询", len(failed))
        for s in list(failed):
            q = get_realtime_quote_mootdx(s)
            if q and q.get("current") is not None:
                quotes[str(s).zfill(6)] = q
                failed.remove(s)
    if failed:
        log.warning("以下股票行情彻底失败，当日跳过: %s", failed[:20])
    return quotes, failed
