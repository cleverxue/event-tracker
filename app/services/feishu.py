# -*- coding: utf-8 -*-
"""飞书通知（规格第六章）：HMAC-SHA256 签名 + 交互式消息卡片。

签名口径（2026-10-18 修正为飞书官方口径）：
  HMAC-SHA256(key=f"{timestamp}\n{secret}", msg="")，timestamp 为秒级
  Unix 时间戳，sign（base64）附在请求体。
  历史：规格原文写的是 key=secret、msg=f"{timestamp}\n{secret}"（与官方
  不同的口径），M4 期仅本地自洽验证、未真实发送；2026-10-18 配好真实
  webhook 后首次推送被飞书服务端拒绝（code=19021 sign match fail），
  curl 验证官方口径通过（code=0），故改为官方口径。真实服务端是最终
  裁判——若日后再改签名算法，必须先真发一张测试卡片。

卡片（规格第六章，2026-10-18 口径）：header 标注"信号触发"；正文含
symbol、事件族、当前价、参照价及类型（avg/fixed/range/ceiling）、偏离度、
触发时 MACD 柱与 CCI(14) 原始值（macd_hist/macd_hist_prev/cci，人工复核用）、
信号给出时间；底部附巨潮原文链接。

依赖约束：只依赖 requests + hmac + hashlib + base64，不引入飞书 SDK。
失败处理：写 logs/notify_failed.log，不重试（由日志告警兜底）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime

import requests

from app import config

log = logging.getLogger(__name__)

NOTIFY_FAILED_LOG = config.PROJECT_ROOT / "logs" / "notify_failed.log"
_SEND_TIMEOUT = 10  # 秒；飞书机器人响应快，失败快速落日志不阻塞引擎

# 参照价类型 → 卡片展示名
_REF_TYPE_LABEL = {
    "avg": "回填均价",
    "fixed": "固定价",
    "range": "区间中值",
    "ceiling": "上限价(辅助)",
}


def sign(secret: str, timestamp: int | None = None) -> tuple[int, str]:
    """生成 (timestamp, sign)。timestamp 秒级 Unix 时间戳。

    飞书官方口径：sign = base64(HMAC-SHA256(key=f"{timestamp}\n{secret}", msg=""))
    （2026-10-18 修正，原规格口径被真实服务端拒绝，见模块 docstring）
    """
    ts = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(f"{ts}\n{secret}".encode("utf-8"),
                      b"", hashlib.sha256).digest()
    return ts, base64.b64encode(digest).decode("utf-8")


def _fmt_price(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:.2f}" if f == f else "-"


def _fmt_pct(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f * 100:.2f}%" if f == f else "-"


def _fmt_num(v, nd: int = 4) -> str:
    """指标原始值展示。None/NaN → '-'。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:.{nd}f}" if f == f else "-"


def build_signal_card(signal: dict, source_url: str = "") -> dict:
    """构造交互式消息卡片（规格第六章字段清单，2026-10-18 口径）。

    signal 至少含：symbol / event_family / current_price / ref_price_used /
    ref_price_type / deviation / macd_hist_at_trigger /
    macd_hist_prev_at_trigger / cci_at_trigger / trigger_time。
    source_url 为巨潮原文直链（底部附注）。
    """
    symbol = signal.get("symbol", "")
    family = signal.get("event_family", "")
    ref_type = signal.get("ref_price_type", "") or ""
    ref_label = _REF_TYPE_LABEL.get(ref_type, ref_type or "-")
    hist = _fmt_num(signal.get("macd_hist_at_trigger"))
    hist_prev = _fmt_num(signal.get("macd_hist_prev_at_trigger"))
    cci = _fmt_num(signal.get("cci_at_trigger"), 1)

    fields = [
        {"is_short": True,
         "text": {"tag": "lark_md", "content": f"**股票**：{symbol}"}},
        {"is_short": True,
         "text": {"tag": "lark_md", "content": f"**事件族**：{family}"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**当前价**：{_fmt_price(signal.get('current_price'))}"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**参照价**：{_fmt_price(signal.get('ref_price_used'))}"
                             f"（{ref_label}）"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**偏离度**：{_fmt_pct(signal.get('deviation'))}"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**MACD柱**：{hist}（前值 {hist_prev}，较前一日"
                             f"{'放大' if hist != '-' and hist_prev != '-' and float(hist) > float(hist_prev) else '收敛'}）"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**CCI(14)**：{cci}（低于 -100 为超卖）"}},
        {"is_short": True,
         "text": {"tag": "lark_md",
                  "content": f"**信号给出时间**：{signal.get('trigger_time', '')}"}},
    ]
    elements: list[dict] = [{"tag": "div", "fields": fields}]
    if source_url:
        elements.append({"tag": "hr"})
        elements.append({"tag": "note", "elements": [
            {"tag": "lark_md", "content": f"公告原文：[巨潮资讯]({source_url})"}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red",
                   "title": {"tag": "plain_text", "content": "信号触发"}},
        "elements": elements,
    }


def build_payload(card: dict, secret: str | None = None,
                  timestamp: int | None = None) -> dict:
    """组装完整请求体（timestamp/sign/msg_type/card）。测试与干跑复用。"""
    secret = secret if secret is not None else config.FEISHU_SECRET
    ts, sign_str = sign(secret, timestamp)
    return {"timestamp": str(ts), "sign": sign_str,
            "msg_type": "interactive", "card": card}


def _log_failure(payload: dict, err: str) -> None:
    """发送失败落 notify_failed.log（JSON 行，含完整载荷便于重放核对）。"""
    NOTIFY_FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "error": err, "payload": payload}
    with NOTIFY_FAILED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.warning("飞书发送失败，已记录 %s: %s", NOTIFY_FAILED_LOG, err)


def send_card(card: dict) -> tuple[bool, str]:
    """发送交互卡片。返回 (是否成功, 说明)。

    未配置 webhook/secret → 直接返回失败（不写日志，属于未启用而非故障）。
    HTTP 异常或业务 code != 0 → 写 notify_failed.log，不重试。
    """
    if not config.FEISHU_WEBHOOK or not config.FEISHU_SECRET:
        return False, "飞书 webhook/secret 未配置（.env），跳过推送"
    payload = build_payload(card)
    try:
        resp = requests.post(config.FEISHU_WEBHOOK, json=payload,
                             timeout=_SEND_TIMEOUT)
        body = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        if resp.status_code == 200 and body.get("code") == 0:
            log.info("飞书卡片发送成功")
            return True, "ok"
        err = f"HTTP {resp.status_code} code={body.get('code')} msg={body.get('msg')}"
    except Exception as exc:  # noqa: BLE001 - 网络异常统一走失败日志
        err = f"{type(exc).__name__}: {exc}"
    _log_failure(payload, err)
    return False, err


def send_signal_card(signal: dict, source_url: str = "") -> tuple[bool, str]:
    """触发引擎入口：构造信号卡片并发送。"""
    return send_card(build_signal_card(signal, source_url))
