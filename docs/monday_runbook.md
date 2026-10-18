# 周一（10-19）运行预案 Runbook

系统从本周起进入全自动日常运行。本文是**每个交易日的检查点清单 + 故障处置卡**。

## 0. 服务形态（2026-10-18 起）

- 启动方式：登录时由启动文件夹 `event-tracker.vbs` 静默调用
  `scripts/start_service.bat` → uvicorn（127.0.0.1:8000）
- 日志：`logs/scheduler.log`（append；每次登录启动会追加一段）
- 手动启停：双击 `scripts/start_service.bat` 启动；
  停止 = 任务管理器结束 python.exe(uvicorn) 或 `taskkill /PID <pid> /F`
- ⚠️ 沙箱安全策略把 schtasks/wscript 列入了黑名单，**任务计划程序方案不可用**，
  当前为启动文件夹等效方案；代价：进程崩溃不会自动重启，
  次日登录才会恢复——所以每日第一个检查点是"服务活着"。

## 1. 每日检查点

| 时间 | 动作 | 预期 |
|---|---|---|
| 登录后任意时点 | `curl -s 127.0.0.1:8000/api/health` | `{"status":"ok","jobs":[3个]}` |
| 14:35 | `tail logs/scheduler.log` | 14:30 引擎开始日志；"腾讯批量实时行情: 827/827 只成功" |
| 14:35 | 查飞书群 | 有触发则收到交互卡片（签名已修正为官方口径，10-18 实测通过） |
| 15:00 | `cat data/signals.csv` | 有触发则新增行；无触发正常（条件苛刻） |
| 16:05 | `tail logs/scheduler.log` | 16:00 初筛完成；信号收盘回填执行 |
| 21:35 | `tail logs/scheduler.log` | 21:30 进展分析完成；均价回填 |

快速命令：

```bash
cd C:/Users/admin/WorkBuddy/2026-10-17-13-58-00/event-tracker
curl -s 127.0.0.1:8000/api/health
tail -20 logs/scheduler.log
python -c "import pandas as pd; print(pd.read_csv('data/signals.csv', dtype=str))"
```

## 2. 故障处置卡

**A. 端口不通 / health 失败**（服务没起来）
→ 双击 `scripts/start_service.bat`；看 `logs/scheduler.log` 尾部找原因
（最常见：8000 被旧实例占用——任务管理器杀掉所有 python 再启动）

**B. 14:30 引擎报错**（日志有 traceback）
→ 先看报错；若是行情/网络类（腾讯限流、RemoteDisconnected），
  等 10-20 分钟手动补跑（引擎幂等性见下）：

```bash
python run_tracker.py --force --no-notify     # 只落库不推送，防重复卡片
# 确认落库无误后，如需推送可单独补发（一般不必，信号已晚点）
```

**C. 补跑的重复推送风险**：signals.csv 是纯追加、无去重。
补跑前先 `cat data/signals.csv` 确认没有当天的 symbol 行；
有则用 `--no-notify` 或先删当天重复行再补跑。

**D. 飞书没收到但有触发信号**（signals.csv 有行、notified=False）
→ `cat logs/notify_failed.log` 看失败原因；
  19021 = 签名/时钟问题（本机时钟漂移也会导致）；
  其他 = webhook 失效（机器人被移出群等），找老哥换新 webhook。

**E. 16:00 收盘回填没执行**（前一日 signals 的 at_close 字段为空）
→ 16:00 任务内部步骤，重启服务不会自动补；
  手动：`python -c "from app.services.signals import backfill_close_states; backfill_close_states()"`
  （以实际函数名为准，见 signals.py）

## 3. 周一（10-19）特别关注

- **14:30 是引擎第一次真实运行**（此前全部为 dry-run/强制验证）。
  重点观察：① 827 只批量行情耗时与限流；② 偏离度幸存者数量；
  ③ 幸存者日线拉取 + MyTT 判定总耗时（决定 14:30-14:35 窗口是否够）。
- 16:00 是板块过滤收紧后的第一次全量初筛（1692 行基础上的增量）。
- 21:30 是全量进展分析第一次完整跑（预估 1-2 小时），
  观察断点续传与限流退避；跑不完不影响次日 14:30（终止类最坏延迟一天，已知边界）。

## 4. 已预演项（10-18）

- 全量 dry-run：候选 1357 事件 / 827 只，腾讯批量行情 827/827 成功（约 15 秒）
- 限量 dry-run（80 条）：两阶段链路完整，33 只日线零限流
- 飞书真实推送：签名官方口径实测通过（10-18 早 07:20，交互卡片送达）
- 常驻链路：start_service.bat 独立拉起验证通过

## 5. 腾讯 WAF 风控应对（10-18 实战教训，重要）

**现象**：全量 dry-run 第二阶段（幸存者逐只拉日线）在约 190 次请求后，
腾讯开始返回 **501 页面**（内容是 waf.tencent.com 风控跳转页）。
持续重试会越封越久；**完全静默停止请求数分钟后才自行恢复**。

**已落地的修复**（2026-10-18）：
1. 日线主端点从 `fqkline/get` 切换到 `kline/kline`（同构数据，
   已交叉验证为不复权口径：其收盘价与即时行情昨收逐一吻合）；
   fqkget 保留为降级端点
2. `market._get_with_backoff` 识别 501/WAF 页 → 抛 `RateLimitedError`
   **不烧重试**，直接判限流
3. 逐只日线间隔 0.4s → 1.0s（对半砍请求速率）
4. 引擎汇总新增“日线限流跳过 N 只”字段；被限流的股票当日跳过判定，
   signals 不写错误数据

**运行期处置**：若 14:30 汇总里"日线限流跳过"非 0，说明又撞墙了——
不要立刻补跑（越打越封），等 30 分钟以上再按故障卡 B 补跑
（`--no-notify` 防重复推送）。日常 827 只批量行情走 qt.gtimg.cn 域，
与日线域分开计数，一般不受影响。
