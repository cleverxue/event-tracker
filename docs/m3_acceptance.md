# M3 验收报告：后端骨架 + 定时任务 + 只读 API

> 项目：个人 A 股公告事件跟踪系统（`event-tracker/`）
> 里程碑：M3（规格第九章）——FastAPI + 三个 CronTrigger + 只读 API
> 日期：2026-10-17

---

## 一、交付物清单

| 文件 | 职责 |
|---|---|
| `app/services/market.py` | 腾讯 qt.gtimg.cn 批量实时行情（fields[3]/[33]/[34]/[4]，GBK）+ 日线（不复权优先）+ mootdx 降级 |
| `app/services/indicators.py` | MyTT 封装：MACD(12,26,9) + CCI(14) + 金叉判定（前值≤0<后值）+ 当日临时 bar 拼接 |
| `app/services/ollama.py` | Ollama 字段回填（仅 price_type=none；JSON-only 提示词；不可达跳过） |
| `app/services/signals.py` | signals.csv 读写（带锁）+ 收盘金叉状态回填（规格 5.1 第 7 步） |
| `app/services/screen.py` | 16:00 任务服务化：初筛 + 增量合并 + Ollama 回填 + 快照 + 信号回填 |
| `app/scheduler/jobs.py` | 三个任务入口（交易日判断 + force 调试参数 + safe_run 兜底） |
| `main.py` | FastAPI + APScheduler BackgroundScheduler（Asia/Shanghai）+ StaticFiles 托管 |
| `app/api/routes.py` | 5 个规格 API + /api/health |
| `tests/test_indicators.py` | 指标/行情层回归（口径硬要求固化） |

## 二、验收结果（对照 M3 硬指标）

| 验收项 | 要求 | 实测 |
|---|---|---|
| FastAPI + 三 CronTrigger 注册 | 16:00/21:30/14:30 | `/api/health` 返回 `["tracker","announcement_screen","progress_analysis"]` ✅ |
| 16:00 任务手动运行 | 无崩溃 | 2.5s 完成：拉取 86 条 → initial 7 → 下载 7/7 → 合并 2037+7→2037（新增 0，幂等去重正确）✅ |
| 当日快照按日生成 | 16:00 后 | `data/snapshots/watchlist_20261017.parquet`（2037 行）✅ |
| 信号回填步骤（5.1 第 7 步） | 正常执行 | 正常空转（signals.csv 暂无信号，M4 产生后生效；代码路径已验证）✅ |
| 21:30 任务手动运行 | 无崩溃 | 10 只小批量：11 事件 / 31 行 timeline / 3 条均价回填 / 7 次状态迁移 ✅ |
| 14:30 任务（M3 占位） | 无崩溃 | watching 1213 只 → 腾讯批量覆盖 **1213/1213，0 失败** ✅ |
| 只读 API | 筛选/timeline/signals | watchlist 筛选 1179（回购 watching）、timeline 9 条状态推进正确、signals 空表正常 ✅ |
| 手动触发 API | trigger/progress | 均返回 ok（force 绕过交易日判断）✅ |
| 回归测试 | — | test_indicators 24 项 + test_classify（分类/进展抽取/代码判定）全绿 ✅ |

## 三、过程中抓出并修掉的 5 个问题

1. **watchlist 写自死锁（最隐蔽）**：`run_screen` 外层持 `WATCHLIST_LOCK`，又调用内部同样要拿这把锁的 `save_watchlist`——`threading.Lock` 不可重入，同线程二次获取直接死锁。现象：无异常、无输出、进程挂住只能强杀。用 `faulthandler.dump_traceback_later` 抓线程栈定位到 `store.save_watchlist`。修法：存储层拆出 `_save_watchlist_locked`（调用方已持锁时用）。
2. **北交所 920 代码段错配**：`to_qt_symbol` 把 920 开头归到 sh，腾讯返回 `v_pv_none_match`——52/1213 只行情拉不到。修正为 43/83/87/920 → bj 后 **1213/1213 全覆盖**。
3. **协议转让进展公告 KeyError**：分类器产出 `progress` 阶段，但状态机映射给了 `in_progress`，而规格 5.2 协议转让状态机没有这个状态。修为“不迁移状态，仅记 timeline + 均价回填”（M2 20 只样本未覆盖此路径，10 只样本跑出）。
4. **`is_trading_day()` 缺参导致 500**：API 层调用未传 `date` 参数。
5. **Ollama 请求被本机代理拦截**：开发机注入了 HTTP_PROXY，发往 127.0.0.1:11434 的请求走代理被重置（ProxyError）。修为专用 Session `trust_env=False`（Docker 部署无代理，不受影响）。同时连接/读超时分离（5s/120s）：Ollama 未启动时 fast fail，不会每条 none 记录都等满超时。

## 四、与规格的差异（最简方案 + 标注）

1. **腾讯批量分批 80 只/批**：规格 5.3 字面为"所有 symbol 逗号拼接，单次请求覆盖全部"，但腾讯对 q= 参数长度有上限（千只级 URL 被拒），取 80 只/批；语义不变（非逐只请求）。
2. **`llm_extracted` 列**：规格 5.1 要求回填结果写入独立字段 llm_extracted，但 4.1 的 schema 表未列该列。按 5.1 执行，watchlist 增加该列（M1/M2 旧文件读取时自动补空串）。
3. **14:30 引擎为 M3 占位**：规格第九章 M4 才实现触发引擎，M3 的 14:30 任务注册后执行"行情层连通性自检"（拉全量 watching 腾讯行情并统计覆盖），非交易日与定时触发均安全跳过。
4. **手动触发接口 force 参数**：规格第七章的 POST /api/trigger/run、/api/progress/run 为调试用途，默认绕过交易日判断直接执行；定时触发仍严格遵守"非交易日 return"。
5. **日线取不复权优先**：14:30 临时 bar 的 close/high/low 来自腾讯即时行情（不复权价），历史日线必须用不复权序列拼接，否则除权股指标失真；不复权不可得时退前复权并记日志。

## 五、三个交易日自动运行观察（10-19 / 10-20 / 10-21）

服务以 `uvicorn main:app --host 127.0.0.1 --port 8000` 后台运行，调度器已注册。观察方式：

```bash
tail -f logs/m3_uvicorn.log                          # 调度器与任务日志
ls data/snapshots/                                   # 每日 16:00 快照
sqlite3  # 无需数据库；watchlist/timeline/signals 均为 Parquet/CSV
```

每日检查点：14:30 占位自检日志 → 16:00 初筛与快照 → 21:30 进展分析。M4 将在观察期后（或并行）实现 14:30 触发引擎本体。

## 六、已知边界

- 21:30 全量进展分析（watching 上千只）预计 1-2 小时（逐只巨潮查询 + 命中公告下载），单人工具可接受；小规模调试用 `run_progress.py --limit N`。
- 交易日历由腾讯指数日线推导，仅到最新已收盘日；未来日期按工作日近似（顺延逻辑已有，国庆后 10 月下旬无节假日）。
- signals.csv 尚无数据，`macd_state_at_close` 回填逻辑待 M4 首个真实信号后验证。
