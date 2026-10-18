# event-tracker GitHub 管理方案

> 2026-10-18 制定。目标：把 event-tracker（M1-M5 已闭环的个人研究工具）纳入  
> Git + GitHub 管理，兼顾**代码可追溯**与**策略/数据不泄露**。方案重决策轻执行，  
> 老哥确认后按第六节命令清单一次性落地。



---

## 一、结论先行（TL;DR）

| 决策项        | 建议                                        | 一句话理由                                        |
| ---------- | ----------------------------------------- | -------------------------------------------- |
| 仓库可见性      | 🔒 **私有仓库**                               | 触发口径、参数、自选股都是个人策略资产，公开=裸奔；私有仓库 GitHub 免费     |
| 仓库名        | `event-tracker`                           | 与目录一致，`github.com/<用户名>/event-tracker`       |
| 分支模型       | **主干开发**：main 单分支                         | 单人工具，不上 PR/评审流；大实验开 `exp/xxx` 临时分支，验证后合回删除   |
| 提交规范       | Conventional Commits + 中文正文               | `feat(tracker): 触发条件改 MACD 柱口径`，历史可读、可 grep  |
| 首次历史       | **单个基线提交** + 里程碑 tag                      | 不伪造 M1-M5 历史；基线打 `v1.0.0`，周五收尾补打 `v1.0.1`…   |
| tools/ 子仓库 | **git submodule**（备选：内化复制）                | 它是 announcement.py 的运行时依赖（sys.path 导入），必须进仓库 |
| CI         | 只跑**离线回归**（tests/ 两个脚本）                   | 联网验收（m4_verify）有腾讯 WAF 风险，不进 CI              |
| 数据/密钥      | 🔒 **永不入库**：.env / data/ / cache/ / logs/ | 见第八节红线表                                      |

**本机前置条件**：git 2.55 ✅ 已可用；`gh` CLI ❌ 未安装（建库用网页端，或先  
`winget install GitHub.cli`）；全局 git 身份 ❌ 未配置（首次 commit 前必须配）。

---

## 二、现状盘点（2026-10-18）

| 项                                                                    | 现状                                                              | 纳入 Git 的处置                       |
| -------------------------------------------------------------------- | --------------------------------------------------------------- | -------------------------------- |
| Git 状态                                                               | event-tracker 非仓库，父目录也非仓库                                       | `git init`，干净起步                  |
| .gitignore                                                           | 无                                                               | 新建（第三节）                          |
| 代码规模                                                                 | Python 约 4,868 行 + 前端 622 行                                     | 全部入库                             |
| `app/` `main.py` `*.py` 脚本                                           | 后端本体                                                            | ✅ 入库                             |
| `frontend/src/` + package.json + **package-lock.json**               | 前端源码（lock 存在，Dockerfile 依赖它）                                    | ✅ 入库                             |
| `frontend/node_modules/`                                             | 依赖实体                                                            | ❌ 忽略                             |
| `frontend/dist/`                                                     | 构建中间产物                                                          | ❌ 忽略（Docker 内重建；本地 npm build 再生） |
| `static/`                                                            | 前端构建产物，**本地非 Docker 运行直接读它**                                    | ✅ 入库（换机免构建可跑）                    |
| `docs/`（6 份验收+runbook）`reports/`（2 份）`README.md`                     | 文档                                                              | ✅ 入库                             |
| `.env`                                                               | **真实 FEISHU_WEBHOOK + SECRET**                                  | 🔒 忽略；`.env.example` 模板 ✅ 入库     |
| `.dockerignore` `Dockerfile` `docker-compose.yml` `requirements.txt` | 部署配置                                                            | ✅ 入库                             |
| `data/`（4.8M：watchlist/audit/快照/日历）                                  | 运行时状态                                                           | ❌ 忽略（第十节引导重建）                    |
| `cache/`（495M 公告 PDF 缓存）                                             | 运行时缓存                                                           | ❌ 忽略                             |
| `logs/`（376K）                                                        | 运行日志                                                            | ❌ 忽略                             |
| `tools/skill-disclosure-event-extractor/`                            | **独立 git 仓库**（remote 指向 quantskills 上游），被 announcement.py 运行时导入 | ⚠️ submodule 或内化，见第七节            |
| `web/`                                                               | 空目录（M3 遗留）                                                      | git 不跟踪空目录，自然消失；可手动删             |

---

## 三、.gitignore 设计（逐项说明）

```gitignore
# ===== 红线：密钥与本地配置 =====
.env

# ===== 运行时数据与状态（新克隆按 docs/github_plan.md 第十节重建）=====
data/
cache/
logs/

# ===== Python =====
__pycache__/
*.pyc
.pytest_cache/

# ===== 前端 =====
frontend/node_modules/
frontend/dist/          # 构建中间产物；服务实际读取 static/（已入库，Docker 内也会重建）

# ===== 系统与临时 =====
Thumbs.db
.DS_Store
*.tmp
*.tmp.parquet
```

设计取舍：

- `data/` 整目录忽略而非挑文件——watchlist.parquet / signals.csv / 审计 CSV /  
  快照全是个人状态，且 parquet 二进制做 diff 无意义。备份走第十节机制。
- `static/` **不忽略**：它是本地非 Docker 运行的直接读取源，且 main.py 的 SPA  
  深链回退依赖它存在；换机克隆后免构建即可起服务。
- `.dockerignore` 已覆盖 `.git/logs/data/cache/.env`，与 `.gitignore` 目标一致，  
  两份文件各管各的构建上下文/版本控制，互不冲突。

---

## 四、分支与提交模型

### 4.1 分支（主干开发）

```
main ─────●─────●─────●─────●─────●─────→   （日常直接提交）
            \         /
             exp/… ─┘                      （大实验：临时分支，验证后合并删除）
```

- 日常修复/口径修订：直接 commit 到 main，当天 push。
- 值得隔离的实验（如换触发引擎结构、试新数据源）：`exp/<主题>` 分支，  
  结论落地后 `merge` 回 main 并删分支。单人项目合并冲突=0，不需要保护规则。

### 4.2 提交规范（Conventional Commits，中文正文）

```
<type>(<scope>): <摘要>

type: feat 新功能 | fix 修复 | docs 文档 | refactor 重构 | test 测试 | chore 杂务
scope: tracker / signals / feishu / frontend / config / docs / deploy …
```

真实示例（本次口径修订）：

```
feat(tracker): 触发条件①由 DIF 线改为 MACD 柱口径

需求方 2026-10-18 复核确认"MACD 较前一日上涨"指 MACD 柱（=(DIF-DEA)×2，
通达信口径）而非 DIF 快线。改动收敛在 indicators.is_macd_hist_rising 一处：
- indicators.compute_states 增补 macd_hist/macd_hist_prev 状态键
- signals.csv 列名 dif_* → macd_hist_*（无历史信号，零迁移）
- 飞书卡片/前端 Signals 页/单测/验收脚本同步切换
```

### 4.3 标签（里程碑）

| tag      | 内容                               |
| -------- | -------------------------------- |
| `v1.0.0` | 基线提交（M1-M5 闭环 + 2026-10-18 口径修订） |
| `v1.0.x` | 之后的每次稳定修订后递增                     |

---

## 五、密钥与数据边界（红线表）

| 资产                            | 位置       | 入库    | 说明                                             |
| ----------------------------- | -------- | ----- | ---------------------------------------------- |
| 飞书 webhook + secret           | `.env`   | 🔒 永不 | `.env.example` 只放占位符；本地 `.env` 靠 .gitignore 挡住 |
| watchlist / signals / 审计 / 快照 | `data/`  | 🔒 永不 | 个人状态；重建见第十节                                    |
| 公告 PDF 缓存                     | `cache/` | 🔒 永不 | 495M，随时可再拉                                     |
| 运行日志                          | `logs/`  | 🔒 永不 | 含完整告警载荷                                        |
| 代码 / 文档 / 部署配置 / static       | —        | ✅     | 随仓库走                                           |

**密钥泄漏应急**（万一 .env 被误推）：① 飞书机器人后台**重置 secret**、  
必要时重建 webhook——旧密钥立即作废，这是唯一必需的止血动作；② 新仓库无历史，  
直接把 `.env` 从工作区删掉再补一次 commit 即可，不需要 filter-repo 洗历史。

---

## 六、首次上线命令清单（确认后照抄执行）

```bash
cd C:/Users/admin/WorkBuddy/2026-10-17-13-58-00/event-tracker

# 0. 身份（全局未配置时才需要；email 建议用老哥的 cleverxue0898@sina.com.cn）
git config user.name  "<老哥的称呼>"
git config user.email "cleverxue0898@sina.com.cn"

# 1. 初始化 + .gitignore（内容见第三节，需先落成文件）
git init
git add .
git commit -m "chore: 初始化仓库（M1-M5 基线 + 2026-10-18 MACD 柱口径修订）"
git branch -M main
git tag v1.0.0

# 2. GitHub 网页端建私有仓库 event-tracker（不要勾选 README/.gitignore 初始化）
git remote add origin https://github.com/<用户名>/event-tracker.git
git push -u origin main
git push origin v1.0.0

# 3. tools/ 子仓库收编为 submodule（见第七节，三种打法选一）
```

**网络前提**：本机访问 github.com 若不通，需先配代理（HTTP(S)\_PROXY 本机已有  
雏形）或改用镜像源；submodule 的 URL 与主库保持一致，别一个直连一个镜像。

---

## 七、tools/ 子仓库处置（重要，运行时依赖）

`app/services/announcement.py` 第 29-31 行把  
`tools/skill-disclosure-event-extractor/scripts` 插入 `sys.path` 导入其  
`fetch_announcements.py`——**16:00 初筛任务强依赖它**。该目录自带 `.git`  
（remote：`ghproxy.net/https://github.com/quantskills/skill-disclosure-event-extractor.git`），  
直接 `git add` 会被当成嵌套仓库跳过，必须显式处置：

| 方案                   | 操作                                                                                                                             | 适合                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| **A. submodule（推荐）** | `git submodule add https://github.com/quantskills/skill-disclosure-event-extractor.git tools/skill-disclosure-event-extractor` | 保留上游归属与更新能力；`.gitmodules` 入库，`git clone --recursive` 自动带上 |
| B. 内化复制              | 删掉子目录的 `.git`，文件直接入库（保留其 LICENSE 归属声明）                                                                                         | 图省心、不追上游更新；344K 体积可接受                                     |
| C. 忽略                | `.gitignore` 加 `tools/`                                                                                                        | ❌ 不可取——16:00 任务会在新克隆上直接崩                                  |

选 A 的注意事项：

- 克隆/换机必须 `git clone --recursive`（或 clone 后  
  `git submodule update --init`），否则 announcement 导入失败；
- Docker 构建上下文包含 tools/（`.dockerignore` 未排除），本地 submodule 已  
  checkout 时镜像构建不受影响；
- 上游是第三方技能，更新前先本地跑 `python m2_verify.py` 回归再接。

---

## 八、CI 方案（GitHub Actions，轻量）

`.github/workflows/ci.yml`：push/PR 到 main 时跑**离线回归**，不联网、不构建前端。

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive      # tools/ 是运行时依赖，必须带上
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"     # 与 Dockerfile python:3.11-slim 对齐
      - run: pip install -r requirements.txt
      - run: python tests/test_indicators.py
      - run: python tests/test_tracker.py
```

边界说明：

- ✅ 覆盖：指标口径（含 MACD 柱判定）、触发判定矩阵、飞书签名已知向量、  
  卡片结构、板块过滤、参照价优先级——全部离线可判；
- ❌ 不覆盖：`m4_verify.py`（真实请求腾讯行情，CI 出口打多了触发 WAF 越打越封）、  
  前端构建（改动本地 `npm run build` + 浏览器实测，固化在 M5 流程里）；
- MyTT==2.9.3 在 PyPI 可装，CI 无需特殊源。

---

## 九、Docker / 镜像（可选，不过度工程化）

- 现有 Dockerfile 多阶段构建（node:20 构建前端 → python:3.11-slim 运行），  
  入库即可复现；**本机无 Docker 未实测**的现状不变，CI 也不加 docker build  
  （慢且没必要）。
- 若哪天想自动推镜像：加一个 workflow 用 `docker/build-push-action` 推  
  `ghcr.io/<用户名>/event-tracker`，依赖 `GITHUB_TOKEN`（无需额外 secret）。  
  当前阶段**建议不做**，需要时再开。

---

## 十、新克隆 bootstrap（从零跑起来的顺序）

```bash
git clone --recursive https://github.com/<用户名>/event-tracker.git
cd event-tracker

# 1. 密钥
cp .env.example .env        # 填 FEISHU_WEBHOOK / FEISHU_SECRET（老哥手里那份）

# 2. 后端依赖（版本已锁死）
pip install -r requirements.txt

# 3. 数据重建（data/ 不入库，二选一）
python scripts/refresh_trade_calendar.py     # 3a. 从头推导交易日历
# 3b. 或从老哥的本地备份恢复 data/watchlist.parquet + signals.csv（见下）

# 4. 前端（static/ 已入库可跳过；仅改了前端源码才需要）
cd frontend && npm ci && npm run build && cd ..

# 5. 起服务
uvicorn main:app --host 127.0.0.1 --port 8000
```

**数据备份机制**（git 不管数据，人管）：

- 每周收尾把 `data/` 下三个关键文件打包：  
  `watchlist.parquet`（1692 行自选）、`signals.csv`（信号与复核原始值）、  
  `trade_calendar.csv`；
- 存放三选一：另一间**私有** GitHub 仓库（如 `event-tracker-data`）/  
  网盘 / 本机第二副本。watchlist 理论上可靠 `build_watchlist.py` 重跑重建  
  （M1 全市场抓取耗时较长），备份仍是更稳的恢复路径。

---

## 十一、日常节奏建议

| 时机                  | 动作                                                                                      |
| ------------------- | --------------------------------------------------------------------------------------- |
| 每次改完代码              | 本地 `python tests/test_indicators.py && python tests/test_tracker.py` 全绿 → commit → push |
| 口径/参数修订（如本次 MACD 柱） | commit message 写清"需求方何时确认、为什么改、改了哪一处"，与 docs/m4_acceptance.md 口径修订段互链                   |
| 每周五收尾               | 递增 tag；把一周 `data/` 关键文件打包备份；看一眼 GitHub Actions 是否全绿                                     |
| 里程碑（新 M 编号）         | README 补一段变更记录，tag                                                                      |

---

## 十二、风险与取舍（如实交底）

| 风险                  | 影响                    | 对策                                                   |
| ------------------- | --------------------- | ---------------------------------------------------- |
| 仓库误设 Public         | 策略全泄（触发口径/参数/自选股）     | 建库时确认 Private；推完在网页端再核一眼                             |
| 忘记 `--recursive` 克隆 | 16:00 初筛崩（tools 导入失败） | bootstrap 第一节已强调；崩了按报错 `git submodule update --init` |
| .env 误推             | 飞书密钥泄漏                | 第五节应急：重置 secret + 删文件补 commit                        |
| 国内访问 GitHub 不稳      | push/clone 失败         | 代理或镜像；主库与 submodule 用一致通道                            |
| data/ 不入库           | 换机/重装后无状态             | 第十节备份机制；watchlist 可重跑但慢                              |
| CI 不覆盖联网链路          | 腾讯侧回归靠人               | m4_verify 仍按 docs/monday_runbook.md 本地执行             |
| 未来单文件变大（>50M）       | git 仓库膨胀              | 目前最大入库文件是 static JS 178K，无压力；真到那天再上 Git LFS          |

---

## 十三、待老哥拍板的三件事

1. **仓库名与归属**：`event-tracker` 可以吗？用哪个 GitHub 账号建？
2. **tools/ 打法**：submodule（推荐，保留上游）还是内化复制（省心）？
3. **CI 要不要**：想要 push 后自动跑离线回归就建 `.github/workflows/ci.yml`  
   （第八节模板直接用）；想极简就先不建，本地跑测试。

确认后我按第六节清单一次性执行（git init → .gitignore → 基线提交 →  
submodule → 推库），CI 文件按第 3 项决定是否落。
