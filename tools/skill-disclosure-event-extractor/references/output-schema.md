# Output Schema — 结构化事件表

输出为 `reports/events/YYYYMMDD.parquet`（可选同名 `.csv`）。一行 = 一个（合并后的）事件。
下游 factor / monitor 技能按 `symbol + ann_date` join 即可复用。

## 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `symbol` | string | ✓ | 带交易所后缀，如 `000001.SZ` / `600000.SH` |
| `sec_name` | string | ✓ | 证券简称（披露时点） |
| `ann_date` | string(YYYYMMDD) | ✓ | 最新披露日 |
| `event_type` | enum | ✓ | 见下方枚举 |
| `event_subtype` | string | ✓ | 见 `event-taxonomy.md` 各类子类型 |
| `tier` | enum{primary,supplement} | ✓ | primary=Pandadata 盲区（核心）；supplement=Pandadata 已结构化，仅取文本细节 |
| `direction` | enum{利好,利空,中性} | ✓ | 规则化标签，**非投资意见** |
| `severity` | enum{高,中,低} | ✓ | 规则化严重度 |
| `subject` | string | – | 事件主体（股东/高管/发函方等） |
| `detail` | string | – | Pandadata feed 无的文本细节（仅 supplement 层填，如平仓线/减持原因） |
| `magnitude` | float | – | 归一后数值（金额→元；比例→小数，如 0.012） |
| `magnitude_unit` | enum{amount,ratio,shares} | – | `magnitude` 的口径 |
| `period` | string | – | 涉及报告期（业绩类），如 `2026Q2` |
| `summary` | string | ✓ | 中性一句话摘要（≤ 80 字，无断言措辞） |
| `stage` | string | – | 事件阶段：预案/进展/完成/终止 |
| `timeline` | list[string] | – | 合并事件的 disclosure_id 时间线 |
| `source_url` | string | ✓ | 公告原文 URL（可回溯核对） |
| `disclosure_id` | string | ✓ | 披露唯一 ID（cninfo announcementId 等） |
| `confidence` | float | ✓ | 0~1，抽取置信度，规则见 taxonomy |
| `fetched_at` | string(ISO8601) | ✓ | 抓取时间（复现审计用） |

## event_type 枚举

```
holding_change | pledge | earnings_preview | regulatory_inquiry
| litigation_guarantee | restructuring | governance | suspension
```

## 空结果约定

某股票在窗口内无公告 → 该股票**不产生行**，但在 run 元数据 `meta.no_disclosure[]` 中登记，
以区分"无披露"与"抓取失败"。

## 示例行（CSV 视角）

```csv
symbol,sec_name,ann_date,event_type,event_subtype,tier,direction,severity,subject,detail,magnitude,magnitude_unit,summary,source_url,disclosure_id,confidence,fetched_at
600000.SH,某公司,20260601,regulatory_inquiry,关注函,primary,利空,中,深交所,,,,收到深圳证券交易所关注函,http://static.cninfo.com.cn/finalpage/...,1223460022,0.9,2026-06-01T20:00:00+08:00
000001.SZ,平安银行,20260612,holding_change,减持意向,supplement,利空,高,控股股东,减持原因;集中竞价,0.015,ratio,控股股东拟减持不超过总股本1.5%,http://static.cninfo.com.cn/finalpage/...,1223456789,0.9,2026-06-13T09:12:03+08:00
```

## 校验契约（validate_events.py 强制项）

1. 必填字段非空：`symbol, sec_name, ann_date, event_type, event_subtype, direction, severity, summary, source_url, disclosure_id, confidence, fetched_at`
2. 枚举合法：`event_type` / `tier` / `direction` / `severity` / `magnitude_unit`
3. 日期格式：`ann_date` 为 8 位 `YYYYMMDD`
4. 溯源非空：`source_url` 以 `http` 开头，`disclosure_id` 非空
5. `magnitude` 非空时 `magnitude_unit` 必须存在
6. `confidence ∈ [0,1]`
