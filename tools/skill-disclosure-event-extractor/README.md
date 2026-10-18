# skill-disclosure-event-extractor

**简体中文** | [English](README.en.md)

将 A 股法定公开披露公告的**非结构化文本**，抽取为**可追溯、可验证的结构化事件表**。

> 📖 **完整使用指南见 [USAGE.md](USAGE.md)** —— 安装、四脚本速查、常见场景（含每日收盘扫自选股利空）、
> 输出解读、回测解读、排错。

- **填补空白**：quantskills 官方预留但为空的「信息检索 / 网络爬取」分类；同时补上 Pandadata
  数据底座缺失的公告全文与细粒度事件。
- **与 `skill-event-risk-alert` 互补上下游**：后者消费 Pandadata **已结构化字段**并**告警**；
  本技能读**公告原文**并**结构化**，输出供其消费。事件分两层：
  - **Tier A 主力**（Pandadata 盲区）：监管问询、诉讼/担保、重组/收购、治理决议、停牌/控制权变更 —— 核心价值。
  - **Tier B 补充**（Pandadata 已有）：增减持、质押、业绩预告 —— 只取文本细节（`detail`）作交叉校验。
- **数据供给层**：输出 parquet/CSV 事件表，供 event-risk-alert / monitor / factor 技能直接 join。

## 结构

```
SKILL.md                     # 工作流 / 抽取规则 / 免责契约
references/
  source-map.md              # 巨潮/沪深路由、限流、降级、合规、缓存
  event-taxonomy.md          # 事件类型 -> 字段 -> direction/severity 规则
  output-schema.md           # 输出字段、枚举、校验契约、示例
scripts/
  fetch_announcements.py     # 公告拉取+下载：--symbols 逐只 / --keywords 全市场搜（发现 Tier A）
  extract_events.py          # 文本→事件：规则分类+字段抽取+方向/严重度+合并（LLM补空可选）
  validate_events.py         # 事件表校验器（字段/枚举/日期/溯源）
  backtest_events.py         # 事件驱动回测：T+1/5/10/20 超额收益（市场中性化），铺 L3 证据
```

## 快速开始

```bash
pip install requests pdfplumber pandas pyarrow

# 1a) 按股票拉公告
python scripts/fetch_announcements.py \
  --symbols 000001.SZ 600000.SH --start 20260101 --end 20260630 --out cache/
# 1b) 或全市场按关键词发现 Tier A 事件（不需预先知道股票）
python scripts/fetch_announcements.py \
  --keywords 关注函 问询函 诉讼 对外担保 重大资产重组 --start 20260101 --end 20260630 --out cache/

# 2) 文本 → 结构化事件表（默认全确定性；加 --llm 启用 Claude 补空字段）
python scripts/extract_events.py --cache cache/ --out reports/events/20260630.csv

# 3) 校验后再发布
python scripts/validate_events.py reports/events/20260630.csv

# 4) （可选）事件驱动回测，产出超额收益证据
python scripts/backtest_events.py reports/events/20260630.csv --out reports/bt.csv
```

## 合规

仅抓取法定公开披露（巨潮 cninfo / 沪深交易所），尊重 robots 与限流，缓存原文保证可溯源、
可复现。不抓取付费/版权内容。

## 免责

> 本报告基于公开披露与规则化抽取生成，仅供研究参考，不构成任何投资建议。

## License

GPL-3.0-only

---

## 📜 License

Copyright (C) 2026 the QuantSkills contributors.

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version. This program is
distributed WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for details.
