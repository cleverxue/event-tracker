# M5 验收报告：前端三页面 + Docker 部署

日期：2026-10-18 ｜ 状态：✅ 已通过（含真实浏览器渲染验收）

## 交付物

| 项 | 文件 | 说明 |
|---|---|---|
| 前端工程 | `frontend/` | Vite 5 + React 18 + React Router 6，三页面只读 |
| 构建产物 | `static/`（源：`frontend/dist/`） | index.html + assets（JS 177KB / gzip 58KB，CSS 4.4KB） |
| 后端适配 | `main.py` | SPA 深链回退（404 异常处理）；`/api/*` 404 保持 JSON |
| 依赖清单 | `requirements.txt` | 10 个运行时依赖，版本锁定本机实测值 |
| 容器部署 | `Dockerfile` / `docker-compose.yml` / `.dockerignore` | 多阶段构建（node:20 → python:3.11-slim），规格第八章口径 |

## 规格符合性（第七章）

| 页面 | 规格要求 | 实测 |
|---|---|---|
| Watchlist | 表格 symbol/event_family/status/price_type/reference_price/avg_price/tracking_expiry | ✅ 七列齐全，价格右对齐三位小数 |
| Watchlist | 按状态和事件族筛选 | ✅ 状态 1692→1659（watching）；叠加事件族 1659→929（回购），表格内容同步 |
| Watchlist | 点击行展开 timeline | ✅ 000821 展开 31 条：日期/标题/“本事件”高亮/状态推进箭头/命中规则/原文链接 |
| Signals | 按时间倒序 | ✅ trigger_time 倒序 |
| Signals | 触发价/参照价及类型/偏离度 | ✅ 12.35 / 12.6 fixed / +2.02%（正偏离红色） |
| Signals | 触发时 vs 收盘时金叉状态对比 | ✅ 300685：MACD 触发时金叉→收盘时**未金叉**（绿），“瞬时金叉后消失”场景正确呈现；601006 收盘时“待回填”（灰） |
| Signals | 链接到对应 watchlist 记录 | ✅ 点击代码跳 `/timeline?symbol=601006` 并自动加载 |
| Timeline | 单只股票公告时间线 | ✅ 601006 19 条 / 300685 29 条 |
| Timeline | 标注属于哪个事件、推进了什么状态 | ✅ 事件归属徽章（回购）+ before → after 状态链（planned→in_progress→completed）+ 命中规则 + event_id + 巨潮原文链接 |

## 验收过程记录

1. **构建**：`npm install`（react/react-dom/react-router-dom + vite）+ `npm run build` 通过；
   产物同步 `static/`（清旧 assets 防残留）。
2. **接口冒烟**：`/`、`/signals`、`/timeline?symbol=600000` 均 200（SPA 回退）；
   `/assets/*.js` 200；`/api/nonexistent` 404 且为 JSON（未被前端吞）。
3. **浏览器实测**（agent-browser + Chromium，截图存档 `logs/m5_*.png`）：
   - Watchlist 首屏渲染、筛选交互、行展开 timeline；
   - Signals 三条测试信号（含已回填/未回填两种状态）、日期筛选（3→1 条）、代码链接跳转；
   - Timeline 深链自动加载与手动输入查询（300685 → 29 条）。
4. **测试数据清理**：验收用 3 条测试信号已删除，`signals.csv` 恢复不存在状态；
   watchlist 1692 行、triggered=0，与验收前一致。

## 验收期发现并修复的 2 个前端 bug

1. **布尔三值混淆**（`Signals.jsx`）：`toBool` 把 `False` 和"空（未回填）"都返回 null，
   导致“收盘时未金叉”显示成“待回填”——恰好掩盖规格要识别的核心场景。
   已改为三值语义（true/false/null），重新构建后验证通过。
2. **Timeline 深链不加载**（`Timeline.jsx`）：从 Signals 跳转时组件新挂载，
   `symbol` 初始 state 已等于 URL 参数，`s !== symbol` 判断永远跳过首次加载。
   改用 `loadedRef` 记录已加载 symbol，修复后深链与手动查询均正常。

## Docker 部署说明（规格第八章）

- 多阶段：`node:20` 构建前端 → `python:3.11-slim` 装 `requirements.txt` →
  dist 复制到 `/app/static` → `uvicorn main:app --host 0.0.0.0 --port 8000`。
- compose：`127.0.0.1:8000:8000`、挂载 `./data` 与 `./cache`、`env_file: .env`、
  `OLLAMA_HOST=http://host.docker.internal:11434`（extra_hosts host-gateway）、
  `restart: unless-stopped`。
- 启动：`cp .env.example .env`（填密钥）→ `docker compose up -d --build` →
  日志 `docker compose logs -f app`。
- **本机无 Docker，镜像未实际构建验证**；前端构建产物与后端服务已在本机
  完整验证（浏览器三页面实测）。依赖版本（pandas 3.0.5 / numpy 2.5.3 等）
  均有 cp311 wheel，python:3.11-slim 可装。

## 新增依赖说明（规格第十章要求）

前端 npm 依赖：`react`、`react-dom`、`react-router-dom`、`vite`、
`@vitejs/plugin-react`。理由：规格第八章明确"node:20 构建前端"，
Vite 为 node 生态标准构建器；React 组件化实现三页面与交互。
**未引入**图表库、UI 组件库、状态管理库（对比用徽章、时间线用原生 CSS）。
后端零新增依赖。

## 遗留事项

- 飞书密钥未配（`.env` 不存在）：信号落库但 `notified=False` 不推送（M4 已知项）。
- signals.csv 目前为空：下一个自然触发在交易日 14:30 自动产生。
- `logs/m5_watchlist.png` / `m5_watchlist_expanded.png` / `m5_signals.png` /
  `m5_timeline.png` 为浏览器实测截图，可人工复核。
