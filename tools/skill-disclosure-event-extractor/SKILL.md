---
name: disclosure-event-extractor
description: >-
  Turn unstructured A-share disclosure text from cninfo (巨潮) and the SSE/SZSE
  exchanges into a traceable, structured event table (监管问询/诉讼担保/重组/治理/
  停牌控制权变更/增减持/质押/业绩预告). Use when the user asks to scan A-share
  announcements, find 关注函/问询函/诉讼/停牌/易主 events, build an event table for
  factor or alert pipelines, or backtest disclosure events. Fills the
  information-search gap Pandadata does not cover.
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-disclosure-event-extractor
  repository_url: https://github.com/quantskills/skill-disclosure-event-extractor
  project_type: skill
  collection: disclosure-event-extractor
  creator: 13817660341-coder
  creator_url: https://github.com/13817660341-coder
  maintainer: 13817660341-coder
  maintainer_url: https://github.com/13817660341-coder
quantSkills:
  project_type: skill
  category: information-search
  tags:
    - a-share
    - disclosure
    - announcement
    - event-extraction
    - cninfo
    - web-scraping
    - event-driven
  platforms:
    - claude-code
    - codex
    - hermes
    - openclaw
    - cursor
  status: stable
  # Pandadata is OPTIONAL here (symbol/name normalization only) — this skill
  # brings its own source (cninfo / SSE / SZSE), which is the whole point.
  optional_requires:
    - skill-pandadata-api
  # 降级自 verified：曾存在千分位金额静默误抽（已修 + 加回归测试），
  # 但仍无外部第三方验证，verified 名不副实。
  validation_level: runnable
  maintainer_type: community
  summary_zh: >-
    从巨潮资讯（cninfo）与沪深交易所抓取法定公开公告原文，经规则抽取为可追溯的结构化事件表
    （类型/主体/日期/金额/方向/严重度）。方向标签经 2022–2024 事件驱动回测校准：利空（尤其
    监管问询）有前瞻信号，利好基本已被 price-in，控制权变更是正偏彩票。
  summary_en: >-
    Extract legally-mandated A-share announcements (cninfo / SSE / SZSE) into a
    source-traceable structured event table. Direction labels are calibrated by a
    2022–2024 event-driven backtest: bearish labels (especially regulatory inquiry)
    carry forward signal; bullish labels are largely priced in.
---

# Disclosure Event Extractor

将 A 股**法定公开披露公告的非结构化文本**，抽取为**可追溯、可验证的结构化事件表**。
本技能是"数据供给层"：它补上 Pandadata 数据底座缺失的公告全文与细粒度事件，
输出可被现有 event-risk / monitor / factor 技能直接 join 使用。

> 定位边界：本技能**只做"文本 → 结构化事件"**，**不做投资判断、不生成买卖建议**。
> 输出是"数据供给层"，可上游对接 `skill-event-risk-alert` 等告警层（见下方「分工」）。

## 已验证信号（2022–2024 回测，详见 references/l3-evidence.md）

标签方向经真实回测校准，可信度分档——**用 `direction` 时先看这张表**：

| 事件 | 标签 | 回测结论 | 可信度 |
|---|---|---|---|
| **监管问询/关注函** | 利空 | 复牌后 T+10 约 **−2% 超额**，三年稳健，跑输占比 57% | ✅ **最强，可用** |
| 一般利空（诉讼/减持等） | 利空 | 横截面稳定跑输大盘 52–58% | ✅ 有效 |
| **控制权变更/易主** | 中性 | 中位 −1.3%、跑赢仅 44%，均值靠尾部借壳个例——**正偏"彩票"** | ⚠️ **单只勿追，仅篮子博尾部** |
| 一般利好（增持/回购/预增） | 利好 | 均值≈0，基本已 price-in | ⚠️ 弱/无预测力 |

> 一句话：**利空（尤其监管问询）标签可信；利好标签普遍已被 price-in；控制权变更是高波动彩票，别当买入信号。**

## 与 skill-event-risk-alert 的分工（重要）

二者是**互补的上下游**，不是竞品——区别在**数据层**：

| | skill-event-risk-alert | 本技能（disclosure-event-extractor） |
|---|---|---|
| 数据源 | Pandadata **已结构化字段接口** | 交易所/巨潮**公告原文文本** |
| 角色 | 消费 / **告警层**（打分、去重、调度、推送） | **数据供给层**（文本→结构化事件） |
| 依赖 | 硬依赖 `skill-pandadata-api` | Pandadata 仅可选 |

**事件分两层**（详见 `references/event-taxonomy.md`）：

- **Tier A 主力层（`tier=primary`）** —— Pandadata **无接口**的事件：`监管问询/关注函`、
  `诉讼/仲裁/对外担保`、`重组/收购/重大合同`、`治理决议(回购/分红/决议)`、`停牌/控制权变更`。
  这是本技能核心价值——`event-risk-alert` 想报警也**无米下锅**。
- **Tier B 补充层（`tier=supplement`）** —— Pandadata **已结构化**、`event-risk-alert` 已覆盖的
  事件：`增减持`、`质押`、`业绩预告`。本技能**不与其争结构化数据**，只在 `detail` 字段抽取
  Pandadata feed 丢失的**文本细节**（平仓线、减持原因、预告措辞差异）作交叉校验；
  下游若已用 Pandadata，Tier B 可整体忽略。

> 一句话：**别人吃 Pandadata 嚼碎的结构化字段并报警；本技能啃 Pandadata 没有的公告原文并结构化。**

---

## 1. 核心工作流（Core Workflow）

按顺序执行，每一步都必须保留溯源（`source_url` / `disclosure_id`）：

1. **确认范围** — 明确股票池（代码或名称）与时间窗（`start_date`~`end_date`，`YYYYMMDD`）。
   代码统一归一为带交易所后缀形式（如 `000001.SZ` / `600000.SH`）。
2. **拉取公告清单** — 优先走巨潮 `cninfo` 公告查询接口；失败降级到沪深交易所披露页。
   路由与降级见 `references/source-map.md`。
3. **下载原文** — PDF → text（优先文本层，扫描件走 OCR 兜底）或 HTML 正文抽取；
   落缓存 `cache/`，记录 `source_url`、`disclosure_id`、`ann_date`、抓取时间。
4. **抽取事件** — 先用 `references/event-taxonomy.md` 的**规则/关键词**做粗分类与字段定位，
   再用 LLM 补齐结构化字段（主体、金额、比例、方向、严重度）。规则命中优先，LLM 仅补空。
5. **归一与去重** — 同一事件多次披露（预案→进展→结果）合并为一条，保留**披露时间线**；
   跨源重复以 `disclosure_id` 去重。
6. **校验** — 运行 `scripts/validate_events.py`：字段完整性、日期一致性、溯源非空、
   枚举值合法。校验不过不得输出。
7. **落盘** — 输出 `reports/events/YYYYMMDD.parquet`（及可选 `.csv`），Schema 见
   `references/output-schema.md`。

---

## 2. 抽取规则（Extraction Rules）

- **事实与判断分离**：事件表只记录**披露事实**（谁、在何时、披露了什么、金额/比例多少）。
  `direction` / `severity` 是**规则化标签**，不是投资意见；标注依据写在 `event-taxonomy.md`，
  不得凭 LLM 自由发挥。
- **方向与严重度**：`direction ∈ {利好, 利空, 中性}`，`severity ∈ {高, 中, 低}`，
  一律按 `event-taxonomy.md` 的成文规则映射；规则未覆盖时置 `中性/低` 并标 `confidence` 偏低。
- **金额/比例归一**：统一单位（金额→元，比例→小数），无法解析置空并降 `confidence`，
  **不得臆造数字**。
- **空结果处理**：某股票在窗口内无公告 → 输出空行集合，明确标注"无披露"，不报错、不编造。
- **时间口径**：按公告披露日 `ann_date` 排序；预告类事件区分"披露日"与"事项发生/生效日"。
- **溯源强制**：每条事件必须带 `source_url` 与 `disclosure_id`，否则视为无效行由校验拦截。

---

## 3. 资源指引（Resource Guide）

- `references/source-map.md` — 巨潮/沪深接口路由、请求参数、限流与降级策略、缓存约定。
- `references/event-taxonomy.md` — 事件类型/子类型 → 抽取字段 → `direction`/`severity` 规则表。
- `references/output-schema.md` — 输出字段定义、枚举、类型、示例行。
- `references/backtest-notes.md` — 事件驱动回测规范：必须市场中性化、样本门槛、confound 清单（实测沉淀）。
- `references/l3-evidence.md` — 2022–2024 正式 L3 回测结果：利空/监管问询标签有前瞻信号（n=4248）。
- `scripts/fetch_announcements.py` — 公告拉取与原文下载（限流/缓存/降级）。两种模式：
  `--symbols`（按股票逐只查）或 `--keywords`（全市场按关键词搜，用于发现 Tier A 事件）。
- `scripts/extract_events.py` — 第4步的独立实现：规则分类+字段抽取+方向/严重度映射+合并去重，
  LLM 补空为可选开关（`--llm`），默认全确定性可离线跑。
- `scripts/validate_events.py` — 事件表校验器（字段/枚举/日期/溯源）。
- `scripts/backtest_events.py` — 事件驱动回测，产出 L2→L3 证据：按 direction/event_type 统计
  T+1/5/10/20 **超额收益**（个股−基准，市场中性化），验证标签是否携带前瞻信息。

---

## 4. 质量标准（Quality Bar）

- **可追溯**：任意一行事件都能通过 `source_url` 回到公告原文；金额/比例可在原文中核对。
- **可复现**：相同股票池+时间窗，重复运行结果一致（缓存原文，抽取规则确定性优先）。
- **审慎措辞**：`summary` 用中性描述（"披露""拟""公告称"），不使用"利好兑现""必将"等断言。
- **合规**：仅抓取法定公开披露（巨潮/交易所），不抓付费/版权内容；尊重 robots 与限流。
  详见 `references/source-map.md` 的合规小节。

---

## 免责声明

> 本报告基于公开披露与规则化抽取生成，仅供研究参考，不构成任何投资建议。
