# -*- coding: utf-8 -*-
"""指标层：MyTT 封装（规格第二章/第三章，口径硬要求）。

硬约束：
- 用 MyTT，不用 TA-Lib（TA-Lib 的 MACD 与国内软件有系统性偏差，
  MyTT 口径与通达信/同花顺一致）
- MACD 参数 (12, 26, 9)，CCI 周期 14（不是 pandas-ta 默认的 20）
- 金叉判定为"当日瞬时状态"：前一日 DIF ≤ DEA 且当日 DIF > DEA，
  基于拼接序列的最后一个点（14:30 触发）或最新收盘点（16:00 回填）

当日临时 bar 构造（规格 5.3）：
  close = 腾讯即时行情 fields[3]（当前价）
  high  = fields[33]（今日最高）、low = fields[34]（今日最低）
  与历史完整日线拼接后送 MyTT，最后一点即当日 14:30 的瞬时状态。

触发条件（2026-10-18 修订，替代双金叉；当日二次修订：DIF 线 → MACD 柱）：
  ① MACD 柱较前一日放大：柱（=(DIF-DEA)×2，通达信口径）当日值 > 前一日值
     （需求方 2026-10-18 复核确认：用 MACD 柱，不用 DIF 线）
  ② CCI 低于 -100：CCI(14) 即时值 < config.CCI_OVERSOLD
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from MyTT import CCI, MACD

from app import config

log = logging.getLogger(__name__)

# 指标计算所需的最少历史根数：CCI(14) 至少 14 根，MACD(12,26,9) 至少 26+9 根，
# 取 60 根保底（EMA 收敛后数值才稳定）
MIN_BARS = 60


def macd_series(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD(12,26,9) → (DIF, DEA, MACD柱)。柱 = (DIF-DEA)×2，通达信口径。"""
    short, long_, signal = config.MACD_PARAMS
    return MACD(closes, short, long_, signal)


def cci_series(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray) -> np.ndarray:
    """CCI(14)。"""
    return CCI(closes, highs, lows, config.CCI_PERIOD)


def is_golden_cross(series: np.ndarray) -> bool:
    """金叉判定：序列最后一点的前一值 ≤ 0 且最后一点 > 0 的穿越语义。

    对 DIF-DEA 序列：前一日 DIF ≤ DEA 且当日 DIF > DEA 即金叉。
    对 CCI 序列：先算 CCI 与其信号线的差，同样口径（见 cci_cross_diff）。
    """
    if len(series) < 2:
        return False
    prev, last = series[-2], series[-1]
    if np.isnan(prev) or np.isnan(last):
        return False
    return bool(prev <= 0 < last)


def cci_cross_diff(cci: np.ndarray) -> np.ndarray:
    """CCI 金叉的差分序列。

    口径说明：国内软件（通达信）CCI 指标本身是一条线，其"金叉"通常指
    CCI 上穿其信号线（CCI 的 N 周期简单移动平均）。本系统按此口径：
    diff = CCI - MA(CCI, N)，diff 由 ≤0 转 >0 即 CCI 金叉。
    """
    n = config.CCI_PERIOD
    if len(cci) < n + 1:
        return np.full(len(cci), np.nan)
    s = pd.Series(cci)
    sig = s.rolling(n).mean().to_numpy()
    return cci - sig


def is_macd_hist_rising(hist: float | None, hist_prev: float | None) -> bool:
    """MACD 柱较前一日放大：柱当日值 > 前一日值（口径为 MACD 柱）。

    柱 = (DIF-DEA)×2（通达信口径，见 macd_series）。"放大"含由负转正、
    负值收窄、正值放大三种情形——柱值走高即算，不要求柱为正。
    需求方 2026-10-18 复核确认：触发条件用 MACD 柱，不用 DIF 线。
    """
    if hist is None or hist_prev is None:
        return False
    return bool(hist > hist_prev)


def is_cci_oversold(cci: float | None,
                    threshold: float | None = None) -> bool:
    """CCI 低于超卖阈值（默认 config.CCI_OVERSOLD = -100）。"""
    if cci is None:
        return False
    limit = config.CCI_OVERSOLD if threshold is None else threshold
    return bool(cci < limit)


def compute_states(history: pd.DataFrame, temp_bar: dict | None = None) -> dict:
    """计算 MACD/CCI 状态。返回 {dif, dif_prev, dea, macd_hist, macd_hist_prev,
    macd_cross, cci, cci_cross, bars}。

    history: 历史完整日线（date, open, close, high, low, volume），正序；
    temp_bar: 当日临时 bar {close, high, low}（14:30 即时口径），
             可选——不传则只算历史序列最后一点（收盘口径）。
    """
    closes = history["close"].to_numpy(dtype=float)
    highs = history["high"].to_numpy(dtype=float)
    lows = history["low"].to_numpy(dtype=float)

    if temp_bar is not None:
        # 规格 5.3：当日 bar 由 当前价/今日最高/今日最低 构造，拼接到历史之后
        closes = np.append(closes, float(temp_bar["close"]))
        highs = np.append(highs, float(temp_bar["high"]))
        lows = np.append(lows, float(temp_bar["low"]))

    if len(closes) < MIN_BARS:
        log.warning("K线根数不足（%d < %d），无法计算指标", len(closes), MIN_BARS)
        return {"dif": None, "dif_prev": None, "dea": None,
                "macd_hist": None, "macd_hist_prev": None,
                "macd_cross": None,
                "cci": None, "cci_cross": None, "bars": len(closes)}

    dif, dea, hist = macd_series(closes)
    cci = cci_series(highs, lows, closes)
    cci_diff = cci_cross_diff(cci)

    return {
        "dif": float(dif[-1]) if not np.isnan(dif[-1]) else None,
        "dif_prev": float(dif[-2]) if len(dif) >= 2 and not np.isnan(dif[-2]) else None,
        "dea": float(dea[-1]) if not np.isnan(dea[-1]) else None,
        # MACD 柱（触发条件①的口径，2026-10-18 需求方复核确认）
        "macd_hist": float(hist[-1]) if not np.isnan(hist[-1]) else None,
        "macd_hist_prev": (float(hist[-2]) if len(hist) >= 2
                           and not np.isnan(hist[-2]) else None),
        "macd_cross": is_golden_cross(dif - dea),
        "cci": float(cci[-1]) if not np.isnan(cci[-1]) else None,
        "cci_cross": is_golden_cross(cci_diff),
        "bars": int(len(closes)),
    }


def fetch_and_compute(symbol: str, temp_bar: dict | None = None,
                      count: int = 320) -> dict | None:
    """拉取日线并计算状态（M4 触发引擎与 M3 信号回填共用）。

    temp_bar 非空 = 14:30 即时口径；为 None = 收盘口径（信号回填用）。
    """
    from app.services import market

    history = market.get_daily_klines(symbol, count=count)
    if history is None or history.empty:
        log.warning("%s 无日线数据，指标计算跳过", symbol)
        return None
    return compute_states(history, temp_bar)
