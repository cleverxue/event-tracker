# 使用指南 · skill-disclosure-event-extractor

把 A 股公告原文（巨潮/交易所）抽成**可溯源的结构化事件表**。本文覆盖安装、四个脚本、
常见场景（含每日收盘扫自选股利空）、当 agent skill 用、输出解读、回测解读。

---

## 目录

1. [安装](#1-安装)
2. [核心概念（先看）](#2-核心概念先看)
3. [四个脚本速查](#3-四个脚本速查)
4. [常见场景](#4-常见场景)
   - [A. 盯自选股，抽公告事件](#a-盯自选股抽公告事件)
   - [B. 全市场找某类事件（Tier A 主力）](#b-全市场找某类事件tier-a-主力)
   - [C. 大规模扫描（只要事件表）](#c-大规模扫描只要事件表)
   - [D. 每天收盘后扫自选股的利空事件 ⭐](#d-每天收盘后扫自选股的利空事件-)
   - [E. 验证事件是否有前瞻信号（回测）](#e-验证事件是否有前瞻信号回测)
5. [当 agent skill 用](#5-当-agent-skill-用)
6. [输出字段解读](#6-输出字段解读)
7. [回测结果怎么读](#7-回测结果怎么读)
8. [排错与注意](#8-排错与注意)

---

## 1. 安装

```bash
cd skill-disclosure-event-extractor
pip install requests pdfplumber pandas pyarrow
```

- `pdfplumber` 抽 PDF 文本；扫描件走 OCR 需另装 `pytesseract`（可选）。
- 只做分类/回测、不抽金额时可用 `--no-text` 跳过 PDF，则 `pdfplumber` 也非必需。

---

## 2. 核心概念（先看）

**管线**：`fetch（拉公告）→ extract（文本→事件）→ validate（校验）→ backtest（可选·验证信号）`

**两层事件**（决定你抓什么）：

| 层 | 事件类型 | 定位 |
|---|---|---|
| **Tier A（主力）** | 监管问询、诉讼/担保、重组/收购、治理决议、停牌/控制权变更 | Pandadata **没有**，本 skill 独家价值 |
| **Tier B（补充）** | 增减持、质押、业绩预告 | Pandadata 已有；只补 `detail` 文本细节，可忽略 |

> 一句话：想要别处拿不到的事件，抓 **Tier A**；尤其**监管问询**回测证明有 ~2% 跑输信号。

**定位边界**：只做"文本→结构化事件"，**不做投资判断、不生成买卖建议**。`direction`/`severity`
是规则化标签，不是投资意见。

---

## 3. 四个脚本速查

| 脚本 | 作用 | 关键参数 |
|---|---|---|
| `fetch_announcements.py` | 拉公告清单+原文 | `--symbols` 逐只 / `--keywords` 全市场搜 / `--no-text` 只取标题 / `--start --end --out` |
| `extract_events.py` | 文本→结构化事件 | `--cache --out` / `--llm`（可选 Claude 补空字段） |
| `validate_events.py` | 校验事件表 | 位置参数：事件表路径（.csv/.parquet） |
| `backtest_events.py` | 事件驱动回测 | 位置参数：事件表 / `--out` / `--benchmark`（默认 000300.SH） |

日期格式统一 `YYYYMMDD`；股票代码带交易所后缀 `000001.SZ` / `600519.SH`。

---

## 4. 常见场景

### A. 盯自选股，抽公告事件

```bash
python scripts/fetch_announcements.py \
  --symbols 000001.SZ 600519.SH --start 20250101 --end 20250630 --out cache/
python scripts/extract_events.py --cache cache/ --out events.csv
python scripts/validate_events.py events.csv
```

### B. 全市场找某类事件（Tier A 主力）

不知道具体股票、按事件找——这是本 skill 最有价值的用法：

```bash
python scripts/fetch_announcements.py \
  --keywords 关注函 问询函 诉讼 对外担保 --start 20250101 --end 20250630 --out cache/
python scripts/extract_events.py --cache cache/ --out events.csv
```

### C. 大规模扫描（只要事件表）

`--no-text` 跳过 PDF 下载，快几十倍（分类是纯标题驱动的，不下正文也能分类）：

```bash
python scripts/fetch_announcements.py --keywords 关注函 立案 减持 \
  --start 20240101 --end 20241231 --out cache/ --no-text
python scripts/extract_events.py --cache cache/ --out events.csv
```

代价：`--no-text` 下 `magnitude`/`detail` 等需正文的字段会缺失，方向仍准（靠标题）。

### D. 每天收盘后扫自选股的利空事件 ⭐

**目标**：每天收盘后，扫自选股当天新披露的**利空**事件，输出一张精简表。

**做法**：用当天日期做时间窗、抽取后筛 `direction == 利空`。存一个脚本 `daily_scan.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /path/to/skill-disclosure-event-extractor

DAY=$(date +%Y%m%d)                       # 今天
WATCH="000001.SZ 600519.SH 300750.SZ"     # 你的自选股
OUT="reports/$DAY"
mkdir -p "$OUT"

# 1) 拉当天公告（当天窗口）
python scripts/fetch_announcements.py --symbols $WATCH \
  --start "$DAY" --end "$DAY" --out "$OUT/cache"

# 2) 抽取事件
python scripts/extract_events.py --cache "$OUT/cache" --out "$OUT/events.csv"

# 3) 校验
python scripts/validate_events.py "$OUT/events.csv"

# 4) 只留利空，生成精简告警
python3 - "$OUT/events.csv" "$OUT/alert_$DAY.csv" <<'PY'
import sys, pandas as pd
src, dst = sys.argv[1], sys.argv[2]
df = pd.read_csv(src)
bad = df[df["direction"] == "利空"][
    ["symbol","sec_name","event_type","event_subtype","severity","summary","source_url"]
]
bad = bad.sort_values("severity")   # 高/中/低
bad.to_csv(dst, index=False)
print(f"利空事件 {len(bad)} 条 -> {dst}")
for _, r in bad.iterrows():
    print(f"  [{r['severity']}] {r['symbol']} {r['sec_name']} {r['event_subtype']}: {r['summary'][:30]}")
PY
```

**定时**（每交易日 18:30，公告基本披露完）：

```bash
chmod +x daily_scan.sh
# crontab -e 加一行（工作日 18:30）：
30 18 * * 1-5 /path/to/daily_scan.sh >> /path/to/daily_scan.log 2>&1
```

> 提示：想覆盖更全的利空面（问询函、诉讼、担保、减持），把自选股方案换成
> `--keywords` 也可，但按 `--symbols` 更贴合"自选股"语义。
> 若在 Claude Code 里，也可用 `/schedule` 建定时云端 agent 跑这套流程。

### E. 验证事件是否有前瞻信号（回测）

```bash
python scripts/backtest_events.py events.csv --out bt.csv --benchmark 000300.SH
```

---

## 5. 当 agent skill 用

把本目录作为 skill 加载进 Claude Code / Codex，然后自然语言下指令，agent 读 `SKILL.md`
按工作流自动执行 fetch→extract→validate：

- "扫一下 000001.SZ、600519.SH 2025 上半年的公告事件"
- "全市场找 2025 Q1 收到监管问询函的公司，输出事件表"
- "每天收盘后帮我扫自选股的利空公告"（agent 会套用场景 D 的思路）

产出带 `source_url` 溯源的事件表，尾部自动附免责声明。

---

## 6. 输出字段解读

一行 = 一个事件。完整字段见 [references/output-schema.md](references/output-schema.md)，关键列：

| 列 | 含义 |
|---|---|
| `symbol` / `sec_name` / `ann_date` | 代码 / 简称 / 披露日 |
| `event_type` / `event_subtype` | 类型 / 子类型（如 `regulatory_inquiry` / 关注函） |
| `tier` | `primary`=Tier A 核心 · `supplement`=Tier B 补充 |
| `direction` | 利好 / 利空 / 中性（**规则化标签，非投资建议**） |
| `severity` | 高 / 中 / 低 |
| `subject` | 事件主体（控股股东 / 交易所 / 证监会…） |
| `magnitude` / `magnitude_unit` | 归一数值 / 口径（amount 元 · ratio 小数） |
| `detail` | Tier B 文本细节（平仓线、减持原因…） |
| `summary` | 中性一句话摘要 |
| `source_url` / `disclosure_id` | 原文链接 / 披露 ID（**可回溯核对**） |
| `confidence` | 抽取置信度 0–1 |

---

## 7. 回测结果怎么读

**只看超额收益（`exret_t*`），不看原始收益**——原始收益被大盘 beta 主导（详见
[references/backtest-notes.md](references/backtest-notes.md) 的 9·24 教训）。

脚本输出三块：
1. **Direction breakdown**：利好/利空各持有期的平均超额收益。
2. **Signal check**：利空跑输占比、利好跑赢占比、利好−利空价差（>50%、价差为正 = 标签有效）。
3. **By event_type**：各类型平均超额。

已验证的 L3 结论（见 [references/l3-evidence.md](references/l3-evidence.md)）：
**利空标签有效、监管问询最强（T+10 约 −2% 超额、三年稳健）；利好标签基本无预测力。**

---

## 8. 排错与注意

| 现象 | 原因 / 解法 |
|---|---|
| `fetch` 返回 0 条 | 别传 `column`（脚本已修）；确认时间窗有交易/披露 |
| PDF 解析失败 `No /Root object` | 扫描件或坏 PDF，脚本 `[warn]` 跳过继续，不影响其他 |
| 事件比公告少很多 | 正常——只按**标题**分类，笼统标题（"关联交易公告"）刻意不臆测（可加 `--llm` 兜底） |
| 港股/B 股混入 | 全市场搜会带 5 位港股码；筛 6 位、首位 `0/3/6` 的 A 股（大样本脚本已内置） |
| 回测很慢 | 每个 symbol 一次行情请求（限流 0.4s）；几千票需几十分钟，属正常 |
| `pandas`/`requests` 未装 | `pip install requests pdfplumber pandas pyarrow` |

**合规**：只抓法定公开披露（巨潮/交易所），尊重 robots 与限流，缓存原文保证可溯源。
不抓付费/版权内容。

---

> 本工具输出仅供研究参考，不构成任何投资建议。
