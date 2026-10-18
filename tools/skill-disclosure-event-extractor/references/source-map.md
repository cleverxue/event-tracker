# Source Map — 公告数据源路由与降级

本技能**只抓取法定公开披露**。数据源按优先级排列，前一个失败才降级到后一个。

## 合规约束（先读）

- 只抓取**巨潮资讯（cninfo）**与**沪深交易所披露平台**的公开公告——法定强制披露、无付费墙、无版权争议。
- **不抓取**：付费研报、新闻媒体正文、社交/论坛内容（版权与反爬风险）。
- 尊重 `robots.txt`；默认请求间隔 ≥ 1s，单会话并发 ≤ 2；命中限流即退避重试（指数退避，最多 5 次）。
- 原文一律**落缓存**（`cache/<disclosure_id>.pdf|html|txt`）并记录抓取时间，保证可溯源、可复现。

---

## 主源：巨潮资讯 cninfo（优先）

公告列表查询（覆盖沪深全市场，含板块过滤）：

- Endpoint: `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`
- 关键参数：
  | 参数 | 说明 | 示例 |
  |---|---|---|
  | `stock` | `代码,orgId`（orgId 由代码映射，见下） | `000001,gssz0000001` |
  | `tabName` | 固定 `fulltext` | `fulltext` |
  | `pageSize` | 每页条数 | `30` |
  | `pageNum` | 页码，从 1 起 | `1` |
  | `seDate` | 时间窗 `start~end` | `2026-01-01~2026-06-30` |
  | `category` | 公告分类码（可选，缩小范围） | `category_bcgz_szsh`（增减持） |

  > ⚠️ **实测坑**：不要传 `column`（szse/sse）——该过滤器过严，对合法的 stock+日期查询会返回
  > `totalRecordNum=0`；**省略 `column`** 由后端按 `stock` 自动判定市场即可正常返回。
  > `fetch_announcements.py` 已按此修正。
- orgId 映射：`GET http://www.cninfo.com.cn/new/data/szse_stock.json`（代码→orgId 全量表，缓存一天）。
- 返回 `announcements[]`：含 `announcementId`（→ `disclosure_id`）、`announcementTitle`、
  `adjunctUrl`（原文相对路径，拼 `http://static.cninfo.com.cn/` 得 `source_url`）、
  `announcementTime`（毫秒时间戳 → `ann_date`）、`secCode` / `secName`。
- 原文下载：`source_url` 通常是 PDF；`scripts/fetch_announcements.py` 用 `pdfplumber` 抽文本层，
  扫描件（无文本层）走 OCR 兜底（可选依赖 `pytesseract`）。

## 降级源：沪深交易所披露平台

cninfo 不可用时降级：

- **上交所**：`http://www.sse.com.cn/disclosure/listedinfo/announcement/`（e-interaction 接口）
- **深交所**：`http://www.szse.cn/disclosure/listed/notice/`（`disclosureInfo` 接口）
- 字段语义与 cninfo 对齐后归一为同一 Schema（见 `output-schema.md`）。

## 分类码速查（category，用于缩小抓取面）

| 事件族 | cninfo category（示意，实际以接口返回为准） |
|---|---|
| 增减持 | `category_bcgz_szsh` |
| 股权质押 | `category_gqbd_szsh` |
| 业绩预告 | `category_yjygjxz_szsh` |
| 监管问询 | `category_jgw询_szsh` / 交易所问询函专栏 |
| 诉讼担保 | `category_ssgg_szsh` / `category_dbzr_szsh` |
| 重组收购 | `category_zjbb_szsh` |
| 股东大会 | `category_gddh_szsh` |

> 分类码随平台调整会变动；`fetch_announcements.py` 支持不传 `category` 全量拉取后本地按
> `event-taxonomy.md` 的关键词规则二次分类，作为分类码失效时的稳态兜底。

## 缓存与幂等

- 缓存键：`disclosure_id`。已缓存则跳过下载，保证重复运行结果一致（幂等）。
- 清单查询结果也缓存（键：`股票池hash + seDate + category`），TTL 默认 6 小时。
