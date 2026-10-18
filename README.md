# A 股公告事件跟踪系统（event-tracker）

个人研究工具：每日盘后从全市场公告中初筛**回购 / 增持 / 协议转让**三类股权交易事件，
构建自选股表（watchlist）；每日 14:30 用腾讯即时行情 + MyTT 指标做触发判定（MACD 柱放大 + CCI 超卖），
偏离度 >1% 时触发买入信号并推送飞书；每日 21:30 拉取公告进展，用状态机跟踪事件
推进/完成/终止，并回填平均成交价。

> 单人使用的研究工具，非生产级量化平台。无数据库、无消息队列、无微服务。

## 里程碑状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 数据质量：拉取/抽取/watchlist 雏形 + 抽检 | ✅ 已完成（见 `docs/m1_acceptance.md`） |
| M2 | 进展管线：21:30 状态机 + 均价回填 | ✅ 已完成（见 `docs/m2_acceptance.md`） |
| M3 | 后端骨架 + 三个定时任务 + 只读 API | ✅ 已完成（见 `docs/m3_acceptance.md`） |
| M4 | 14:30 触发引擎 + 飞书签名卡片 | ✅ 已完成（见 `docs/m4_acceptance.md`） |
| M5 | 前端三页面 + Docker 部署 | ✅ 已完成（见 `docs/m5_acceptance.md`） |

运维与工程文档：`docs/monday_runbook.md`（周一检查点与故障处置卡）、
`docs/github_plan.md`（GitHub 仓库管理方案，含 .gitignore/分支/CI/密钥红线）。

> **选股范围（需求方 2026-10-17 确认，硬约束）**：仅**沪深主板 + 创业板**，
> 剔除科创板（688/689）、北交所（430/830/870/920）、B 股（900/200）。
> 入库侧（16:00 初筛、build_watchlist）与引擎侧（14:30）双重过滤；
> 存量数据已由 `scripts/filter_boards.py` 清理（2037 → 1692 行，
> 备份 `data/watchlist_pre_board_filter_20261017.parquet`）。

## 目录结构

```
event-tracker/
├── app/                        # 后端（FastAPI + APScheduler 同进程）
│   ├── config.py               # 业务参数（硬约束，勿随意改）
│   ├── main.py                 # FastAPI 入口：调度器注册 + StaticFiles 托管
│   ├── api/routes.py           # 只读 API + 手动触发
│   ├── scheduler/jobs.py       # 三个定时任务入口（交易日判断 + force 调试参数）
│   ├── storage/
│   │   └── store.py            # watchlist/timeline 线程安全读写（Lock + 原子写）
│   └── services/
│       ├── announcement.py     # 公告拉取（巨潮 fulltextSearch + 原文下载 + 逐只查询）
│       ├── extract.py          # 三事件族规则抽取（价格/双方/比例/进展累计量）
│       ├── progress.py         # M2 进展管线（状态机 + 均价回填）
│       ├── trading_calendar.py # 交易日历（腾讯指数日线推导）
│       ├── market.py           # 行情层：腾讯批量实时 + 日线 + mootdx 降级
│       ├── indicators.py       # 指标层：MyTT MACD(12,26,9)/CCI(14) + 金叉/MACD柱放大/CCI超卖判定
│       ├── ollama.py           # Ollama 字段回填（price_type=none 时）
│       ├── screen.py           # 16:00 任务服务层（初筛+合并+快照+信号回填）
│       ├── signals.py          # signals.csv 读写 + 收盘原始值回填（macd_hist/macd_hist_prev/cci）
│       ├── tracker.py          # M4 14:30 触发引擎（偏离度+MACD柱放大&CCI<-100+信号落库）
│       └── feishu.py           # M4 飞书 HMAC-SHA256 签名 + 交互卡片
├── main.py                     # uvicorn 入口
├── build_watchlist.py          # M1 主脚本：构建 watchlist 雏形
├── run_progress.py             # M2 主脚本：公告进展 + 状态机 + 均价回填
├── run_tracker.py              # M4 主脚本：手动跑 14:30 触发引擎（调试/验收）
├── sample_check.py             # M1 抽检脚本（随机 50 条）
├── m2_verify.py                # M2 自动验收脚本
├── m4_verify.py                # M4 自动验收脚本（含限量实跑）
├── requirements.txt            # 后端依赖（版本锁定为本机实测通过版本）
├── Dockerfile                  # 多阶段构建：node:20 前端 → python:3.11-slim 后端
├── docker-compose.yml          # 单容器部署（规格第八章口径）
├── scripts/
│   ├── refresh_trade_calendar.py
│   ├── filter_boards.py        # 板块清理（存量 watchlist 一键过滤，先备份）
│   └── probe_cninfo*.py        # 接口探测脚本（开发期验证巨潮接口能力）
├── tests/test_classify.py      # 分类器回归测试（49 分类 + 14 价格 + 6 进展 + 16 代码）
├── tests/test_indicators.py    # 指标口径/行情解析回归（MACD 柱口径、金叉语义、腾讯字段）
├── tests/test_tracker.py       # M4 回归（板块过滤/参照价优先级/触发矩阵/飞书签名/卡片）
├── frontend/                   # M5 前端（Vite + React + React Router，只读三页面）
│   ├── src/pages/              #   Watchlist / Signals / Timeline
│   └── dist/                   #   构建产物（npm run build 后生成）
├── tools/skill-disclosure-event-extractor/   # quantskills 公告技能（GPL-3.0）
├── data/                       # watchlist.parquet / timeline.parquet / signals.csv / 审计 CSV / 交易日历 / 快照
├── cache/                      # 公告原文缓存（PDF+TXT，幂等可断点续传）
├── static/                     # 前端构建产物落地的目录（main.py 由 StaticFiles 托管）
└── logs/                       # 运行日志
```

## 快速开始（M1，本地开发）

环境：Python 3.11+（开发用 3.13，代码按 3.11 兼容编写；Docker 内为 3.11-slim）。

```bash
# 1. 安装依赖
pip install pandas pyarrow requests pdfplumber

# 2. 刷新交易日历（tracking_start 对齐用，可定期重跑）
python scripts/refresh_trade_calendar.py

# 3. 构建 watchlist（过去一年；缓存幂等，中断后重跑同一命令即可续传）
python build_watchlist.py --start 20251018 --end 20261017 --reset

# 4. 抽检（随机 50 条，输出 reports/m1_sample_check.md）
python sample_check.py

# 5. 分类器回归测试
python tests/test_classify.py
```

## 进展分析（M2，21:30 任务的批量版）

```bash
# 非交易日会直接退出；--force 强制执行（验证/回填用）
python run_progress.py --limit 20 --force          # 20 只股票验证（族间轮询取样）
python run_progress.py --symbol 600519 --force     # 单只股票
python run_progress.py --family 回购 --force       # 只跑回购族
python run_progress.py --force                     # 全量 watching/triggered
python run_progress.py --limit 20 --force --reset-progress   # 重置 M2 字段后重跑（重验证用）
```

产出：`data/timeline.parquet`（规格 4.2 状态迁移）+ watchlist 的
`avg_price`/`avg_price_source`/`status` 回填 + `data/m2_progress_audit.csv`
（逐条公告抽取审计，人工核对用）。

**状态机**（规格 5.2）：回购 planned→in_progress→completed/terminated；
增持 planned→in_progress→completed/failed；协议转让 agreed→approved→
completed/terminated。状态只进不退、终态粘滞；终态记录 status 置
completed/terminated，过期置 expired。**均价回填**：进展/完成类公告下载正文，
`avg_price = 累计金额 / 累计股数`，来源公告 ID 记入 `avg_price_source`。

常用参数：

- `--start/--end`：`YYYYMMDD`，公告发布日窗口
- `--reset`：忽略既有 `watchlist.parquet` 从零重建（批量回填用；日常增量不要加）
- `--limit N`：只处理分类后的前 N 条初始公告（调试）
- `--titles-only`：只拉标题分类，不下载原文（快速验证）

## 触发引擎（M4，14:30 任务的批量版）

```bash
# 非交易日会直接退出；--force 强制执行（盘后/周末验证用）
python run_tracker.py --force --limit 30            # 抽样 30 条事件验证
python run_tracker.py --force --symbol 600519       # 单只股票
python run_tracker.py --force --dry-run             # 全量干跑（不写库不推送）
python run_tracker.py --force --no-notify           # 落信号但不推飞书
python m4_verify.py                                 # M4 自动验收（7 步）
```

**触发条件**（2026-10-18 修订，替代原“双金叉”；当日复核确认①用 MACD 柱、
不用 DIF 线）：偏离度 = (参照价 - 当前价) / 当前价 > 1%，且当日 14:30
技术条件满足——① MACD 柱较前一日放大（柱 =(DIF-DEA)×2，通达信口径，
柱值走高即算；改口径动 `indicators.is_macd_hist_rising` 一处）；
② CCI(14) 低于 -100（超卖区）。均基于腾讯即时行情构造的当日临时 bar：
close=当前价、high=今日最高、low=今日最低。参照价优先级：
avg_price（21:30 回填）> price_fixed > range 中值；ceiling 型偏离度仅
辅助展示、技术条件满足即触发。触发后回写 status=triggered、追加 signals.csv
（macd_hist/macd_hist_prev/cci 原始值 + 信号给出时间 trigger_time +
推送时刻 notified_time，次日 16:00 回填收盘时同口径原始值，供人工复核）、
推飞书交互卡片（HMAC-SHA256 签名，2026-10-18 起为飞书官方口径）；推送失败写
`logs/notify_failed.log`，不重试。

**进入自选记录**：每条 watchlist 记录 added_time（进入时刻，秒级）+
entry_basis（来源渠道 + 公告标题原文）；存量 1692 行为 M1 初建补录，
added_time 如实留空、entry_basis 标注“存量补录”。前端 Watchlist 页
“进入自选”列与行展开面板可见，供复核每只股票“因何、何时进来”。

**选股范围**：仅沪深主板 + 创业板（剔除科创板 688/689、北交所
430/830/870/920、B 股 900/200），入库与引擎双侧过滤。存量一键清理：

```bash
python scripts/filter_boards.py            # 先备份后过滤（原子写）
python scripts/filter_boards.py --dry-run  # 只看剔除报告
```

## 服务运行与 API（M3）

```bash
# 安装 M3 依赖
pip install fastapi uvicorn apscheduler MyTT mootdx

# 启动（uvicorn 与 APScheduler 同进程；默认 127.0.0.1，内网访问自行改绑）
uvicorn main:app --host 127.0.0.1 --port 8000
```

启动后自动注册三个定时任务（Asia/Shanghai，非交易日自动跳过）：

| 任务 | 时间 | 内容 |
|---|---|---|
| announcement_screen | 16:00 | 全市场初筛（仅沪深主板+创业板）+ watchlist 增量 + Ollama 回填 + 当日快照 + 信号收盘回填 |
| progress_analysis | 21:30 | 自选股公告进展 + 状态机 + 均价回填 + 清理 90 天前快照 |
| tracker | 14:30 | 触发引擎：偏离度 >1% 且 MACD柱较前一日放大 且 CCI(14)<-100 → signals.csv + 飞书交互卡片 |

只读 API（无认证）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchlist?status=&family=` | 列表，支持筛选 |
| GET | `/api/watchlist/{symbol}/timeline` | 单只股票事件时间线 |
| GET | `/api/signals?from=&to=` | 历史信号（含 macd_hist/macd_hist_prev/cci 触发时与收盘时原始值、trigger_time、notified_time） |
| GET | `/api/health` | 服务与任务状态 |
| POST | `/api/trigger/run` | 手动触发 14:30 引擎（调试用，force 绕过交易日判断） |
| POST | `/api/progress/run` | 手动触发 21:30 进展分析（调试用；全量约 1-2 小时，小规模用 `run_progress.py --limit N`） |

配置：复制 `.env.example` 为 `.env`，填 `FEISHU_WEBHOOK`、`FEISHU_SECRET`、
`OLLAMA_HOST`（默认 `http://host.docker.internal:11434`）、`OLLAMA_MODEL`（默认 `qwen3:8b`）。
密钥不落代码；Ollama 不可达时自动跳过回填并记日志，不阻塞主流程。
飞书密钥未配置时信号正常落库（`notified=False`）、不推送；配置后需**重启服务**
生效（环境变量在进程启动时读取）。

## 前端三页面（M5）

技术栈：Vite 5 + React 18 + React Router 6（第一版只读，无前端编辑）。
npm 依赖仅 react / react-dom / react-router-dom，构建工具 vite +
@vitejs/plugin-react；**未引入图表库与 UI 组件库**（对比用徽章、时间线用
原生 CSS），保持单人工具的体量。

三个页面（规格第七章）：

| 页面 | 路径 | 内容 |
|---|---|---|
| Watchlist | `/` | 表格（代码/事件族/状态/价格类型/参照价/成交均价/跟踪截止）+ 状态与事件族筛选 + 点击行展开该股公告时间线 |
| Signals | `/signals` | 触发信号倒序 + 日期筛选 + 触发时与收盘时 MACD柱/CCI(14) 原始值对比（收盘脱离超卖区等复核场景）+ 代码链接跳 Timeline |
| Timeline | `/timeline` | 单只股票公告时间线：事件归属徽章、状态推进（before → after）、巨潮原文链接 |

本地开发与构建：

```bash
cd frontend
npm install
npm run dev      # 开发服务器 5173，/api 自动代理到 127.0.0.1:8000
npm run build    # 产物在 frontend/dist
```

构建后同步到后端静态目录并重启服务（非 Docker 方式）：

```bash
rm -f static/index.html
cp -r frontend/dist/* static/
uvicorn main:app --host 127.0.0.1 --port 8000
```

深链说明：`/signals`、`/timeline?symbol=xxx` 直接刷新由 main.py 的
SPA fallback（404 异常处理）回退 index.html；`/api/*` 的 404 仍返回
JSON，不会被前端页面吞掉。

## Docker 部署（规格第八章）

```bash
# 1. 配置密钥（复制示例后填写；飞书不配则只落库不推送）
cp .env.example .env

# 2. 构建并启动（多阶段：node:20 构建前端 → python:3.11-slim 运行）
docker compose up -d --build

# 3. 查看日志
docker compose logs -f app
```

compose 口径（与规格一致）：端口映射 `127.0.0.1:8000:8000`（仅本机可访问）；
挂载 `./data` 与 `./cache`；`env_file: .env`；`OLLAMA_HOST` 指向
`host.docker.internal:11434`（extra_hosts host-gateway）；`restart: unless-stopped`。
后端依赖锁定在 `requirements.txt`（本机实测版本）；镜像内不装 TA-Lib 等
编译依赖（规格禁用）。

## 数据说明

- **watchlist.parquet**：字段严格遵循规格 4.1。`symbol` 为纯 6 位代码；
  `publish_date/tracking_start/tracking_expiry` 以 `datetime64[ns]`（零点）存储，
  语义为 date；`event_id` 取自公告 `disclosure_id`。
- **m1_extraction_audit.csv**：全量抽取审计（含未入 watchlist 的进展/终止类公告、
  证据片段、原文链接），抽检与人工核对都基于它。
- **cache/**：按 `disclosure_id` 缓存 PDF/TXT，重复运行不重复下载。

## 已知边界（M1）

1. **同一回购项目的多份初始公告**（预案 + 报告书）会形成两行 watchlist，
   状态机在 M2 会对同族 watching 行统一推进——多项目个股存在归属歧义，已知限制。
2. **“提议回购”提示性公告**（实控人/董事长提议，早于董事会预案）不入 watchlist：
   规格状态机从“回购报告书/预案”起算。此类公告在审计表中标记 unclassified。
3. **以协议转让方式减持**的公告会按关键词进入协议转让族（规格关键词驱动，
   未要求排除减持）。
4. 交易日历截至最后一个已知交易日；对发布日为最新交易日的公告，tracking_start
   走工作日兜底（周六日顺延到周一），节假日内可能偏差一天。

## 合规

仅抓取巨潮资讯网（cninfo）法定公开披露；请求间隔 ≥1s、并发 ≤2、失败指数退避；
原文全部落缓存保证可溯源。`tools/skill-disclosure-event-extractor` 为 GPL-3.0
项目（Copyright QuantSkills contributors），保留其 LICENSE。
