# -*- coding: utf-8 -*-
"""三事件族规则抽取（M1）。

输入公告标题与正文，输出 watchlist 行字段 + 审计证据。
设计原则（对齐 skill 的分类哲学：标题驱动、不臆造）：
- 分类与阶段只看标题（正文噪音大，标题已标准化）
- 正文本地化后匹配：去全部空白（pdfplumber 常插空格）、繁简关键用字归一
  （A+H 公司走港股规则披露繁体公告）、╱→/，再跑正则
- 价格解析不出即 price_type="none"（交 M3 的 Ollama 回填），绝不错配

说明：skill 的 extract_events.py 输出为事件风险向 schema（direction/severity），
不含 watchlist 需要的价格字段，故本模块在其缓存的原文之上自建三事件族抽取层，
并复用其金额/比例正则的同源思路（千分位容错）。此为本系统对规格的
"未覆盖细节取最简方案"处理，已在交付说明中标注。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 繁简关键用字归一（仅覆盖股权交易语境常见字，A+H 繁体公告用）
# --------------------------------------------------------------------------- #
_TRAD2SIMP = {
    "購": "购", "價": "价", "擬": "拟", "資": "资", "總": "总", "額": "额",
    "幣": "币", "億": "亿", "萬": "万", "實": "实", "際": "际", "於": "于",
    "並": "并", "關": "关", "聯": "联", "應": "应", "該": "该", "擔": "担",
    "責": "责", "聲": "声", "確": "确", "產": "产", "權": "权", "變": "变",
    "動": "动", "證": "证", "監": "监", "發": "发", "稱": "称", "簡": "简",
    "讓": "让", "過": "过", "戶": "户", "記": "记", "終": "终", "協": "协",
    "議": "议", "進": "进", "劃": "划", "銷": "销", "續": "续", "減": "减",
    "轉": "转", "託": "托", "買": "买", "賣": "卖", "餘": "余", "圓": "圆",
    "係": "系", "為": "为", "還": "还", "這": "这", "時": "时", "號": "号",
    "報": "报", "來": "来", "後": "后", "個": "个", "們": "们", "務": "务",
    "賣": "卖", "億": "亿", "證": "证", "據": "据", "簽": "签", "署": "署",
    "變": "变", "項": "项", "額": "额", "權": "权",
}
_TRAD_TABLE = str.maketrans(_TRAD2SIMP)


def normalize_text(text: str) -> str:
    """去全部空白 + 繁简归一 + 除号归一，供正则匹配。"""
    squashed = re.sub(r"\s+", "", text)
    squashed = squashed.translate(_TRAD_TABLE)
    return squashed.replace("╱", "/").replace("／", "/")


# --------------------------------------------------------------------------- #
# 标题分类：事件族 + 阶段
# --------------------------------------------------------------------------- #
# 分类两步走：先过"家族关键词门禁"（标题必须含家族词），再匹配阶段正则。
# 门禁不可省——否则"增持计划实施完毕"会被回购族的"实施完毕"截胡。
# 门禁之下，阶段正则用家族无关关键词即可，且必须如此：
# "回购方案实施完成""增持股份计划实施完成"这类标题在家族词与完成词之间
# 插了字，要求连续的"增持计划实施完成"会整类漏匹配（实测 126 条完成公告
# 误入 initial、混进 watchlist）。每族按 terminated/completed(failed) →
# progress → initial 顺序匹配：
# "关于终止回购股份方案的公告" 必须先命中终止，不能落到"回购方案"。
# 用正则窗口而非子串，兼容真实标题的花样：
#   - "回购A股股份方案"（A+H 港股式标题，关键词中间插 A股）
#   - "回购股份方案实施进展"（进展词不连续）
#   - "解除《股份转让协议》"（解除与协议中间隔书名号内容）
# 规格 5.2 的关键词映射在此基础上面向真实标题做了最小扩写
# （方案/预案→planned、实施结果/实施完成→completed、含"进展"→in_progress、
# 协议转让获批→approved 等），已在交付说明中标注。
_FAMILY_KEYWORD = {
    "回购": re.compile(r"回购"),
    "增持": re.compile(r"增持"),
    "协议转让": re.compile(r"协议转让|股份转让"),
}

_STAGE_REGEXES: dict[str, list[tuple[str, re.Pattern]]] = {
    "回购": [
        ("terminated", re.compile(r"终止|停止回购")),
        ("completed", re.compile(r"实施完毕|实施完成|实施结果|回购完成|回购结果|期限届满")),
        ("progress", re.compile(r"进展")),
        ("initial", re.compile(r"回购[^。；，,]{0,6}(?:方案|预案|报告书)")),
    ],
    "增持": [
        ("failed", re.compile(r"未实施|未能实施|终止|期限届满|增持失败")),
        ("completed", re.compile(r"实施完毕|实施完成|实施结果|增持完成|增持结果")),
        ("progress", re.compile(r"进展")),
        ("initial", re.compile(r"增持[^。；，,]{0,6}(?:计划|方案)|拟增持")),
    ],
    "协议转让": [
        ("terminated", re.compile(r"终止|解除")),
        ("completed", re.compile(r"过户|登记完成")),
        # approved：协议转让的监管审批环节（规格 5.2 状态机 agreed→approved→
        # completed/terminated，但规格未列关键词，此处按真实标题最小扩写：
        # 国资委/证监会/市监局批复类公告。家族门禁已要求标题含"协议转让/
        # 股份转让"，误伤面可控）
        ("approved", re.compile(r"获批|获得批准|批准|审核通过|审查通过|过会|核准|无异议"
                                r"|通过[^。；，,]{0,10}审查")),
        ("progress", re.compile(r"进展")),
        ("initial", re.compile(r"协议转让|股份转让协议|股份转让")),
    ],
}


def classify_detail(title: str) -> tuple[str | None, str | None, str]:
    """标题 → (event_family, stage, matched_rule)。

    matched_rule 为命中的标题片段（如"回购股份方案实施进展"），
    供 timeline 溯源与前端展示；无命中返回空串。
    """
    t = normalize_text(title)
    for family, kw in _FAMILY_KEYWORD.items():
        if not kw.search(t):
            continue
        for stage, pattern in _STAGE_REGEXES[family]:
            m = pattern.search(t)
            if m:
                # 股权激励"限制性股票定向回购"是员工股份收回，非二级市场回购，
                # 其价格对跟踪端无意义，不入 watchlist（实测北交所 920 系高发）
                if family == "回购" and stage == "initial" and "限制性股票" in t:
                    continue
                return family, stage, m.group(0)[:24]
    return None, None, ""


def classify_announcement(title: str) -> tuple[str | None, str | None]:
    """标题 → (event_family, stage)。

    stage ∈ initial / progress / completed / terminated / failed / approved。
    watchlist 只收 initial（初始公告）；其余阶段由 M2 状态机消费。
    """
    family, stage, _ = classify_detail(title)
    return family, stage


# --------------------------------------------------------------------------- #
# 价格字段抽取
# --------------------------------------------------------------------------- #
# 千分位必须容忍：高价股（如茅台 1,887.63 元/股）必然带逗号，
# 裸 \d+ 会静默漏抽——与 skill 对金额正则的教训同源。
# 区间分隔符不含连字符（半角/全角"-"/"－"）：调价公式"27.10元/股-0.1元/股=27元/股"
# 的减法会被误判为价格区间（实测 5 例）；合法区间实测均为 至/到/~。
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CUR = r"(?:人民币|RMB|￥)?"          # 价格前的币种前缀，可选
RE_RANGE_BOTH = re.compile(rf"不低于{_CUR}{_NUM}元/股[^。；;]{{0,40}}?不超过{_CUR}{_NUM}元/股")
RE_RANGE_TO = re.compile(rf"{_CUR}{_NUM}元/股(?:至|到|~|～|—){_CUR}{_NUM}元/股")
RE_FIXED = re.compile(rf"(?:转让价格|交易价格|回购价格|增持价格|成交价格|初始价格)为{_CUR}{_NUM}元/股")
RE_CEILING = re.compile(rf"(?:不超过|不高于|上限(?:为|是)?|价格上限(?:为|是)?){_CUR}{_NUM}元/股")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


# 股权激励语境的前置标记：回购报告书常顺带披露"限制性股票激励计划的回购价格"，
# 与本次二级市场回购无关（实测豫园股份 600655 误抽 5.37 元/股）。
# 命中价格短语时回看前文 40 字，含这些标记的匹配跳过。
_EQUITY_INCENTIVE = ("限制性股票", "激励计划", "股权激励")


def _match_clean(pattern: re.Pattern, text: str) -> re.Match | None:
    """第一个前文不含股权激励语境的匹配。"""
    for m in pattern.finditer(text):
        ctx = text[max(0, m.start() - 40):m.start()]
        if not any(k in ctx for k in _EQUITY_INCENTIVE):
            return m
    return None


@dataclass
class PriceExtraction:
    price_type: str = "none"
    price_low: float | None = None
    price_high: float | None = None
    price_fixed: float | None = None
    evidence: str = ""  # 命中片段，供抽检核对


def _evidence(text: str, m: re.Match, pad: int = 45) -> str:
    i = m.start()
    return text[max(0, i - pad): i + len(m.group(0)) + pad]


def extract_price(title: str, body: str) -> PriceExtraction:
    """从标题+正文抽价格语义（本地化后的拼接串，标题优先）。

    语义映射（规格 4.1）：
      不低于X且不超过Y → range(low=X, high=Y)
      X元/股至Y元/股  → range
      （协议）转让价格为X → fixed
      不超过/上限X     → ceiling
      仅"不低于"或都没有 → none（交 Ollama 回填）
    """
    text = normalize_text(title) + "。" + normalize_text(body)
    m = _match_clean(RE_RANGE_BOTH, text)
    if m:
        low, high = _to_float(m.group(1)), _to_float(m.group(2))
        if low > high:
            low, high = high, low
        return PriceExtraction("range", low, high, None, _evidence(text, m))
    m = _match_clean(RE_RANGE_TO, text)
    if m:
        low, high = _to_float(m.group(1)), _to_float(m.group(2))
        if low > high:
            low, high = high, low
        return PriceExtraction("range", low, high, None, _evidence(text, m))
    m = _match_clean(RE_FIXED, text)
    if m:
        return PriceExtraction("fixed", None, None, _to_float(m.group(1)),
                               _evidence(text, m))
    m = _match_clean(RE_CEILING, text)
    if m:
        return PriceExtraction("ceiling", None, _to_float(m.group(1)), None,
                               _evidence(text, m))
    return PriceExtraction("none")


def reference_price(p: PriceExtraction) -> float | None:
    """参照价（规格 4.1）：fixed 取固定值；range 取中值；ceiling 取上限；none 为 NaN。"""
    if p.price_type == "fixed":
        return p.price_fixed
    if p.price_type == "range" and p.price_low is not None and p.price_high is not None:
        return (p.price_low + p.price_high) / 2
    if p.price_type == "ceiling" and p.price_high is not None:
        return p.price_high
    return None  # none → NaN，调用方转 float('nan')


# --------------------------------------------------------------------------- #
# 交易双方 / 比例（审计与 Ollama 提示词参照用，不入 watchlist）
# --------------------------------------------------------------------------- #
RE_COUNTERPARTY = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()·．.、-]{2,30})"
    r"（以下简称[\"“]?(转让方|受让方|增持主体|减持主体|收购方|出让方)[\"”]?）")
RE_SHARE_RATIO = re.compile(
    r"占(?:公司)?总股本(?:的)?(?:比例)?(?:为|是)?(\d+(?:\.\d+)?)\s*%")


def extract_counterparty(title: str, body: str) -> str:
    text = normalize_text(title) + "。" + normalize_text(body)
    m = RE_COUNTERPARTY.search(text)
    return m.group(1) if m else ""


def extract_share_ratio(title: str, body: str) -> float | None:
    text = normalize_text(title) + "。" + normalize_text(body)
    m = RE_SHARE_RATIO.search(text)
    return float(m.group(1)) / 100 if m else None


# --------------------------------------------------------------------------- #
# 进展公告的累计量抽取（M2 21:30 均价回填：avg_price = 累计金额 / 累计股数）
# --------------------------------------------------------------------------- #
# 股数句式（实测覆盖）：
#   "累计回购股份数量为1,234,567股" / "累计增持公司股份2,500,000股"
#   "累计买入了公司股票合计97,600股"（员工持股计划：动词后带"了"、用"股票"）
#   "过户数量合计为10,500,000股"（协议转让过户完成，无"累计"前缀）
#   "合计转让其持有的公司股份1,050万股"（万股单位）
# 只认"累计/已累计/共计"前缀的常规句式，避免把方案上限
# "回购数量不超过1,000,000股"当成实际值；协议转让过户类无"累计"前缀，
# 走"过户/转让股份数量为X股"分支。
# 金额支持 万/亿/元 三档："支付的总金额为1,876.54万元"、
# "累计成交金额12亿元"、"转让价款总额为18,765,432.00元"；
# 动词限定为 使用/支出/投入/支付/成交/增持/回购 + 资金/金额/价款/对价，
# 排除"成交的最高价格15.50元/股"这类价格描述。
# 多值处理（实测 688311 过户公告：转让总价 3.24 亿 + 四个受让方分笔
# 226M/57M/19M/21.6M）：同一文档多个不同取值时，优先"合计/共计/总额/
# 总价"限定的那个；仍无法区分则放弃回填——算错均价比没有均价更危险
# （错误偏离度会产生虚假信号）。
_NUM_W = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(万)?"
_AMT = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(亿|万)?元"
RE_PROG_SHARES = re.compile(
    rf"(?:累计|已累计|共计)(?:回购|增持|买入|购买)了?(?:的)?(?:公司)?"
    rf"(?:股份|股票)?(?:数量)?(?:为|是|达|合计|共计)*{_NUM_W}股")
RE_XFER_SHARES = re.compile(
    rf"(?:过户|转让)(?:的)?(?:股份|股票)?(?:数量)?(?:为|是|达|合计|共计)*{_NUM_W}股")
RE_PROG_AMOUNT = re.compile(
    rf"(?:累计)?(?:已)?(?:使用|支出|投入|支付|成交|增持|回购)了?(?:的)?(?:总)?"
    rf"(?:资金|金额|价款|对价)(?:总额)?(?:为|是|达|合计|共计)*(?:人民币)?{_AMT}")
RE_XFER_AMOUNT = re.compile(
    rf"(?:转让总价|转让总价款|转让价款|转让对价|交易对价|价款总额|转让价格|股份转让价款)"
    rf"(?:总额)?(?:为|是|合计|共计)*(?:人民币)?{_AMT}")

_UNIT = {None: 1.0, "万": 1e4, "亿": 1e8}
_TOTAL_KW = ("合计", "共计", "总额", "总价")


def _extract_cumulative(patterns: tuple[re.Pattern, ...], text: str,
                        label: str) -> float | None:
    """收集全部匹配 → 单值直接用；多值优先"合计/总价"限定；否则放弃。"""
    matches: list[re.Match] = []
    for pat in patterns:
        matches.extend(pat.finditer(text))
    if not matches:
        return None
    values = [float(m.group(1).replace(",", "")) * _UNIT[m.group(2)]
              for m in matches]
    distinct = sorted(set(values))
    if len(distinct) == 1:
        return distinct[0]
    for m, v in zip(matches, values):
        if any(k in m.group(0) or k in text[max(0, m.start() - 12):m.start()]
               for k in _TOTAL_KW):
            return v
    log.warning("累计%s多值不一致 %s，放弃回填（保守）", label, distinct[:5])
    return None


def extract_progress(body: str) -> tuple[float | None, float | None]:
    """进展/完成公告 → (累计股数, 累计金额[元])。解析不出返回 (None, None)。"""
    text = normalize_text(body)
    shares = _extract_cumulative((RE_PROG_SHARES, RE_XFER_SHARES), text, "股数")
    amount = _extract_cumulative((RE_PROG_AMOUNT, RE_XFER_AMOUNT), text, "金额")
    return shares, amount
