# -*- coding: utf-8 -*-
"""Ollama 回填服务（规格 5.1 第 3 步）。

仅对规则抽取后 price_type="none" 的记录调用宿主机 Ollama，
提示词要求模型只返回 JSON，字段固定为
price_low / price_high / price_fixed / counterparty / share_ratio，
并附原文片段作为依据。回填结果写入独立字段 llm_extracted（JSON 字符串），
不覆盖规则抽取结果。

降级：Ollama 不可达/超时/返回不可解析时跳过并记日志，不阻塞主流程。
"""
from __future__ import annotations

import json
import logging

import requests

from app import config

log = logging.getLogger(__name__)

# 专用 session 并禁用环境代理：开发机注入了 HTTP_PROXY，会拦截发往
# 127.0.0.1:11434 / host.docker.internal 的本地请求（ProxyError）。
# Ollama 在宿主机/容器网络内直连，禁用代理不影响 Docker 部署形态
# （容器内本就没有代理变量）。
_session = requests.Session()
_session.trust_env = False

# 8B 模型 CPU 推理慢，读超时放宽到 120s；连接超时单独收紧到 5s——
# Ollama 未启动时连接被拒/黑洞会立即失败，不可让每条 none 记录都等满超时
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 120

_PROMPT_TEMPLATE = """你是上市公司公告信息抽取助手。下面是一份公告的标题与正文片段。
请仅依据原文抽取本次股权交易的价格与对手方信息，严格返回 JSON（不要输出任何
其他文字、不要用 markdown 代码块）。字段固定为：
{{
  "price_low": 数字或 null,      // 回购/增持价格区间下限（元/股），无则 null
  "price_high": 数字或 null,     // 价格区间上限（元/股），无则 null
  "price_fixed": 数字或 null,    // 固定价格（元/股），无则 null
  "counterparty": "字符串或 null", // 交易对手方名称（协议转让必填）
  "share_ratio": 数字或 null,     // 转让股份占总股本比例（%），无则 null
  "evidence": "支撑上述结论的原文片段（30字以内）"
}}
注意：股权激励对象的授予价格、员工限售股回购价格不是本次交易价格，
不要抽取；原文没有的信息一律 null，不要推测。

【标题】{title}

【正文片段】
{body}
"""


def backfill_fields(title: str, body: str,
                    model: str | None = None) -> dict | None:
    """调 Ollama 回填字段。成功返回 dict，失败/不可解析返回 None。"""
    if not config.OLLAMA_HOST:
        log.debug("OLLAMA_HOST 未配置，跳过回填")
        return None
    model = model or config.OLLAMA_MODEL
    # 正文截断：控制推理时长（价格与对手方信息集中在公告前段）
    snippet = body[:3000]
    prompt = _PROMPT_TEMPLATE.format(title=title, body=snippet)
    try:
        resp = _session.post(
            f"{config.OLLAMA_HOST.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0}},
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
    except requests.RequestException as exc:
        log.warning("Ollama 不可达（%s），跳过回填: %s", config.OLLAMA_HOST, exc)
        return None

    # 解析：容忍模型包裹的 markdown 代码块
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        log.warning("Ollama 返回不可解析（无 JSON 对象）: %s", text[:120])
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        log.warning("Ollama 返回 JSON 解析失败: %s | %s", exc, text[:120])
        return None

    result = {
        "price_low": _as_float(data.get("price_low")),
        "price_high": _as_float(data.get("price_high")),
        "price_fixed": _as_float(data.get("price_fixed")),
        "counterparty": data.get("counterparty") or None,
        "share_ratio": _as_float(data.get("share_ratio")),
        "evidence": (data.get("evidence") or "")[:200],
        "model": model,
    }
    if all(result[k] is None for k in
           ("price_low", "price_high", "price_fixed")):
        log.info("Ollama 未抽出价格字段（可能原文确实无价格语义）")
    return result


def _as_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_json_str(result: dict | None) -> str:
    """回填结果 → watchlist.llm_extracted 列的 JSON 字符串（空串表示无回填）。"""
    if not result:
        return ""
    return json.dumps(result, ensure_ascii=False)
