# M4 验收报告：14:30 触发引擎 + 飞书通知（+ 选股范围收紧）

> 项目：个人 A 股公告事件跟踪系统（`event-tracker/`）
> 里程碑：M4（规格第九章）——触发引擎本体 + 飞书签名卡片
> 附加：需求方 2026-10-17 新增硬约束——选股范围收紧为仅沪深主板 + 创业板
> 日期：2026-10-17

---

## 一、交付物清单

| 文件 | 职责 |
|---|---|
| `app/services/tracker.py` | 14:30 触发引擎：候选过滤 → 批量行情 → 偏离度一级门控 → 日线+临时 bar 二级金叉判定 → signals.csv 落库 + status=triggered 回写 + 飞书卡片 |
| `app/services/feishu.py` | 飞书通知：HMAC-SHA256 签名（规格口径）+ 交互式消息卡片 + 失败落 `logs/notify_failed.log` 不重试 |
| `run_tracker.py` | M4 CLI：`--force/--symbol/--limit/--dry-run/--no-notify` |
| `tests/test_tracker.py` | 58 项回归：板块过滤/参照价优先级/偏离度/触发矩阵/飞书签名已知向量/卡片结构/原文直链 |
| `m4_verify.py` | M4 自动验收脚本（7 步，含强制触发全链路验证与事后恢复） |
| `app/config.py` | 新增 `ALLOWED_PREFIXES` + `is_allowed_symbol()`（板块硬约束） |
| `app/services/screen.py`、`build_watchlist.py` | 入库侧板块过滤（16:00 初筛与 M1 重建共用） |
| `scripts/filter_boards.py` | 存量 watchlist 一键清理（先备份后过滤、原子写、快照同步刷新） |
| `app/scheduler/jobs.py` | 14:30 任务由 M3 占位自检切换为真引擎 |
| `app/api/routes.py` | `/api/trigger/run` 文档更新（引擎已实现） |

## 二、验收结果（对照规格第九章 M4 + 新增板块约束）

| 验收项 | 要求 | 实测 |
|---|---|---|
| 14:30 引擎 | 即时金叉判定 + 偏离度 >1% 触发 + signals.csv 落库 | ✅ 两阶段判定（先偏离度后金叉），干跑/实跑/强制触发三路验证 |
| 飞书签名 | HMAC-SHA256(key=secret, msg=f"{timestamp}\n{secret}")，秒级时间戳，sign 附请求体 | ✅ 已知向量 `ioThkp939qla5DvK7aF+h4LQRcKvYhihlJbNgHokY3s=` + 独立复算一致 |
| 交互卡片 | header"信号触发"；symbol/事件族/当前价/参照价及类型/偏离度/瞬时 MACD/CCI；底部巨潮原文链接 | ✅ 结构断言全过；链接由 `finalpage/日期/announcementId.PDF` 推导（M1 实测形态） |
| 手动触发历史信号 | 验证卡片格式和签名正确 | ✅ 步骤 5 真实数据建卡 + 步骤 7 强制触发走完「落库→回写→重建卡片」全链路 |
| 腾讯批量覆盖全部 watching | 批量请求覆盖 | ✅ **827/827 = 100%**（80 只/批，M3 已录该偏差；板块清理后原 52 只北交所失败源消除） |
| 当日临时 bar 构造 | 当前价/最高/最低拼接后 MyTT 输出合理 | ✅ 3 只样本：bars=历史+1、DIF/DEA/CCI 数值合理、金叉状态为布尔（如 600519：DIF=6.7630 DEA=-3.2830 CCI=146.9） |
| 选股范围（新增约束） | 仅沪深主板+创业板 | ✅ 存量 2037→**1692** 行（剔除科创板 260 + 北交所 85）；入库侧+引擎侧双重过滤；回归 19 组代码段判定全绿 |

## 三、触发判定口径（实现解释，需求方留意）

规格 5.3 的金叉判定段落同段覆盖 MACD 与 CCI（"前一日 DIF ≤ DEA 且当日 DIF > DEA；CCI 同理"），signals.csv 亦同时记录两指标状态。实现取**双金叉**语义：MACD 金叉 **且** CCI 金叉。若偏好"仅 MACD"或"任一"，改 `tracker.should_trigger` 一处即可（单测已覆盖两种边界的矩阵）。

其余口径（均为规格原文或最保守解释）：

1. **偏离度**：`(ref_price - current_price) / current_price > 0.01`，严格大于（恰 1% 不触发，已单测）。
2. **参照价优先级**：`avg_price（非空）> price_fixed > range 中值`。
3. **ceiling 型**：偏离度仅作辅助展示、不门控——金叉即触发，卡片仍展示偏离度。
4. **none 型且无均价回填**：无参照价无法判定偏离度，跳过不触发（规格未列，按"宁缺毋滥"；此类事件多数会在 21:30 均价回填后获得 avg_price 参照）。
5. **行情失败降级**：腾讯批量 → mootdx 逐只 → 当日跳过并记日志，不发错误信号。
6. **性能取向**：偏离度门控在前、日线拉取在后（触发是"且"关系）；日线逐只拉取间隔 0.4s 避开本机出口短时限流；同 symbol 多事件共享一次日线。

## 四、与规格的差异（最简方案 + 标注）

1. **飞书签名口径以规格为准**：规格为 `key=secret, msg=f"{timestamp}\n{secret}"`，与飞书官方文档（`key="timestamp\nsecret"`、msg 为空）不同。若真实机器人验签失败，先核对机器人后台"签名校验"开关与 secret 一致性。
2. **批量 80 只/批**（M3 已录）：腾讯 q= 参数长度上限所致，语义不变。
3. **原文链接推导**：watchlist schema 无链接列，卡片底部链接按 M1 实测形态 `static.cninfo.com.cn/finalpage/{公告日}/{announcementId}.PDF` 由 event_id + publish_date 推导，零额外请求。
4. **notified 语义**：未配置 webhook 时为 False 且不写 notify_failed.log（属未启用而非故障）；配置 `.env` 后需重启服务生效（env 在进程启动时读取）。
5. **强制触发验收事后恢复**：`m4_verify.py` 步骤 7 会 patch 判定模拟条件达标跑真实写入链路，验证后删除信号行、状态回 watching——复跑安全，不留验收痕迹。

## 五、运行状态与数据

- watchlist：**1692 行**（回购 938 / 协议转让 390 / 增持 364）；watching 事件 1357 条涉及 **827 只**股票
- 备份：`data/watchlist_pre_board_filter_20261017.parquet`（清理前 2037 行全量留档）
- timeline：38 行属于被剔除事件（审计底稿保留，API 不可见）
- signals.csv：暂无自然触发信号（偏离度+双金叉条件苛刻，限量样本 75 条事件 0 自然触发）；下一个自然触发将在**交易日 14:30 自动发生**，16:00 任务回填收盘金叉状态供复盘
- 单测：`test_tracker` 58 项 + `test_indicators` + `test_classify` 全绿

## 六、已知边界

- **飞书未实发**：`.env` 未创建（无 FEISHU_WEBHOOK/FEISHU_SECRET），推送路径经本地签名复算与载荷结构验证，未做真实发送。需求方提供密钥（或自建 `.env`）并重启服务后，交易日 14:30 自动推送。
- 14:30 全量引擎预计 3-8 分钟（827 只行情分批 + 幸存者日线逐只），单人工具可接受。
- "金叉收盘消失"现象按规格设计通过次日 16:00 回填 `macd/cci_state_at_close` 复盘留痕，不消除。
- 21:30 与 14:30 的状态交互：triggered 事件不再参与 14:30 复扫（status 已非 watching），21:30 进展分析仍覆盖（与 M2 一致）。

---

## 2026-10-18 口径修订（需求方提出，已落地）

### 1. 触发条件：双金叉 → MACD 柱放大 + CCI 超卖

| 项 | 原口径 | 新口径 |
|---|---|---|
| 技术条件 | MACD 与 CCI 均即时金叉（前值≤0<后值） | ① MACD 柱较前一日放大（柱 =(DIF-DEA)×2，通达信口径，柱值今日 > 昨日）② CCI(14) < -100 |
| 偏离度门控 | >1%（ceiling 型仅金叉即触发） | >1%（ceiling 型仅技术条件即触发，结构平移） |
| signals 记录 | 金叉布尔 ×4 | macd_hist/macd_hist_prev/cci 原始值（触发时+收盘时） |

口径决策（2026-10-18 当日二次修订，老哥复核确认）：**"MACD 较前一日上涨"按
MACD 柱实现**——柱 =(DIF-DEA)×2（通达信口径），柱值走高即"放大"（含由负转正、
负值收窄、正值放大三种情形）。初版曾按 DIF 线实现并预留复核接口，本次按复核
结果落定为柱口径，改动收敛在 `indicators.is_macd_hist_rising` 一处
（`compute_states` 同步增补 `macd_hist`/`macd_hist_prev` 两个状态键，
signals.csv / 飞书卡片 / 前端 Signals 页列名随之切换为 macd_hist_*）。

### 2. watchlist 新增两列（可复核性）

- `added_time`：进入自选时刻（秒级）。16:00 初筛写入 `datetime.now()`；存量 1692 行无从考证，**如实留空**
- `entry_basis`：来源渠道 + 公告标题原文，如"16:00 初筛｜国瓷材料：关于回购公司股份方案的公告"；
  存量行由 `scripts/backfill_entry_info.py` 从 M1 审计 CSV 关联标题补录（1692/1692 关联成功）

### 3. signals 时间记录

- `trigger_time` = 买入信号给出时间（秒级，既有字段，本次确认精度与展示）
- `notified_time` = 飞书实际推送成功时刻（新增；未推送为空）
- 飞书卡片新增"信号给出时间"字段，MACD/CCI 展示原始值

### 4. 修订后实测（2026-10-18 11:xx）

- 三套回归全绿（test_tracker 触发矩阵按新口径重写：CCI 恰 -100 不触发/严格小于等边界）
- m4_verify 重跑：限量 75 事件 0 自然触发（新条件更苛刻：跳过原因 MACD未涨 9、
  CCI 未达 -100 约 14、无参照价 7+15），22 次日线拉取零限流
- 强制触发全链路通过（落库→状态回写→卡片重建→恢复原状）
- UI 实测：Signals 页正确展示"触发时 -132.5 超卖 → 收盘 -45.2 脱离超卖区"、
  待回填灰徽章、已推送时刻；Watchlist 页"进入自选"列（存量显示"—（存量）"）与
  展开面板"进入依据"（无 timeline 的股票也可见）
