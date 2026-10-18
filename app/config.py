# -*- coding: utf-8 -*-
"""全局业务参数与路径配置。

第三章业务参数为需求方确认的硬约束，勿随意改动；
路径/密钥走环境变量，密钥不落代码。
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# 业务参数（规格第三章，硬编码）
# --------------------------------------------------------------------------- #
TRACKING_WINDOW_DAYS = {
    "回购": 360,
    "增持": 60,
    "协议转让": 1800,
}
DEVIATION_THRESHOLD = 0.01          # 偏离度 > 1%
MACD_PARAMS = (12, 26, 9)           # 与通达信一致
CCI_PERIOD = 14                     # 与通达信一致，不是 pandas-ta 默认的 20
# 触发条件（需求方 2026-10-18 修订，替代原"MACD 且 CCI 双金叉"）：
#   ① MACD 柱较前一日放大——口径为 MACD 柱（=(DIF-DEA)×2，通达信口径）
#      当日值 > 前一日值（当日复核确认用柱、不用 DIF 线；若需改回 DIF 线，
#      改 indicators.is_macd_hist_rising 一处）
#   ② CCI 低于 -100（CCI(14) 即时值 < -100，超卖区）
CCI_OVERSOLD = -100

# --------------------------------------------------------------------------- #
# 选股范围硬约束（需求方 2026-10-17 确认）：仅沪深主板 + 创业板
# 剔除：科创板（688/689）、北交所（430/830/870/920 段）、B 股（900/200）
# 落地位置：16:00 初筛入口、14:30 引擎取价前、存量 watchlist 一次性清理
# （scripts/filter_boards.py，先备份后过滤）
# --------------------------------------------------------------------------- #
ALLOWED_PREFIXES = (
    "600", "601", "603", "605",   # 沪市主板
    "000", "001", "002", "003",   # 深市主板
    "300", "301",                 # 创业板
)


def is_allowed_symbol(symbol: str) -> bool:
    """是否在选股范围内（沪深主板 + 创业板）。"""
    code = str(symbol).strip().split(".")[0].zfill(6)
    return code.startswith(ALLOWED_PREFIXES)


# 14:30 触发时，当日 bar 的 close/high/low 取自腾讯即时行情
# fields[3]/fields[33]/fields[34]，不是收盘价
# 金叉判定为当日 14:30 的瞬时状态，非上一完整交易日收盘状态

SCHEDULE = {
    "announcement_screen": {"hour": 16, "minute": 0},   # 全市场初筛 + watchlist 增量 + 信号回填
    "progress_analysis":   {"hour": 21, "minute": 30},  # 自选股公告进展 + 状态机 + 均价回填
    "tracker":             {"hour": 14, "minute": 30},  # 触发引擎（即时金叉判定）
}
TIMEZONE = "Asia/Shanghai"

# --------------------------------------------------------------------------- #
# 事件族与数据模型枚举（规格第四章）
# --------------------------------------------------------------------------- #
EVENT_FAMILIES = ("回购", "增持", "协议转让")
PRICE_TYPES = ("fixed", "range", "ceiling", "none")
WATCHLIST_STATUS = ("watching", "triggered", "completed", "terminated", "expired")

# 事件族状态机（规格 5.2）
STATE_MACHINE = {
    "回购": {
        "planned": ["回购报告书", "回购预案", "回购方案", "回购股份预案", "回购股份方案"],
        "in_progress": ["回购进展"],
        "completed": ["回购完成", "实施完毕", "回购实施结果"],
        "terminated": ["终止回购"],
    },
    "增持": {
        "planned": ["增持计划", "增持方案"],
        "in_progress": ["增持进展"],
        "completed": ["增持完成"],
        "failed": ["未实施", "终止增持"],
    },
    "协议转让": {
        "agreed": ["股份转让协议", "协议转让"],
        "completed": ["过户完成", "登记完成"],
        "terminated": ["终止协议", "解除协议", "协议解除"],
    },
}

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CACHE_DIR = PROJECT_ROOT / "cache"           # 公告原文缓存（PDF/TXT）
STATIC_DIR = PROJECT_ROOT / "static"         # 前端构建产物（M5）
TRADE_CALENDAR_PATH = DATA_DIR / "trade_calendar.csv"
WATCHLIST_PATH = DATA_DIR / "watchlist.parquet"
TIMELINE_PATH = DATA_DIR / "timeline.parquet"
SIGNALS_PATH = DATA_DIR / "signals.csv"

for _d in (DATA_DIR, SNAPSHOT_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# 外部服务（密钥走环境变量，见 .env.example）
# --------------------------------------------------------------------------- #
def _load_dotenv(path) -> None:
    """极简 .env 加载器（零依赖，规格禁引入未列出依赖）。

    Docker 下由 compose 的 env_file 注入环境变量，本加载器不覆盖已存在
    的变量（setdefault 语义）；本地/任务计划运行时负责把 .env 读进
    os.environ。只支持 KEY=VALUE 逐行解析，忽略注释与空行，
    值可带单/双引号（引号会被剥掉）。
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


_load_dotenv(PROJECT_ROOT / ".env")

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# 腾讯行情字段映射（规格第二章，GBK 编码）
QT_FIELDS = {
    "current": 3,    # fields[3] 当前价
    "prev_close": 4,  # fields[4] 昨收
    "high": 33,      # fields[33] 今日最高
    "low": 34,       # fields[34] 今日最低
}

# 快照保留天数（规格 4.4）
SNAPSHOT_KEEP_DAYS = 90
