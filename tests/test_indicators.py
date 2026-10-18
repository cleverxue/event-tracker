# -*- coding: utf-8 -*-
"""指标层与行情层回归测试（M3）。

覆盖：
- MyTT MACD(12,26,9) 口径：柱 = (DIF-DEA)*2（通达信口径）
- MACD 柱放大判定：is_macd_hist_rising（2026-10-18 触发条件①口径，
  需求方复核确认用柱不用 DIF 线）
- CCI(14) 计算与金叉差分
- 金叉判定语义：前值 ≤0 < 后值
- 当日临时 bar 拼接（14:30 口径）：close/high/low 各影响对应序列
- 腾讯字段解析（fields[3]/[33]/[34]）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import indicators, market  # noqa: E402

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"  ✓ {name}")
    else:
        failures += 1
        print(f"  ✗ {name} {detail}")


# --------------------------------------------------------------------------- #
# 构造可控序列：先跌后涨，制造一次确定的 DIF 上穿 DEA
# --------------------------------------------------------------------------- #
def _trend_series():
    # 60 根下跌 + 60 根上涨，保证 MACD 在尾部出现金叉
    down = np.linspace(50, 30, 60)
    up = np.linspace(30, 60, 60)
    return np.concatenate([down, up])


print("== MACD 口径 ==")
closes = _trend_series()
dif, dea, bar = indicators.macd_series(closes)
check("DIF/DEA/柱 三元组返回", len(dif) == len(closes) and len(dea) == len(closes))
# MyTT 内部对 DIF/DEA 返回值 round 到 3 位、柱用未 round 值计算，
# 存在 0.002 级尾差（不影响金叉判定——只用 DIF/DEA 差），故放宽到 0.01
check("柱 ≈ (DIF-DEA)*2（通达信口径）",
      np.allclose(bar[-10:], (dif[-10:] - dea[-10:]) * 2, atol=0.01))
check("尾部 DIF > DEA（趋势反转后）", dif[-1] > dea[-1],
      f"dif={dif[-1]:.4f} dea={dea[-1]:.4f}")

print("== 金叉判定 ==")
check("穿越语义：前≤0<后 → True", indicators.is_golden_cross(np.array([-1.0, 0.5])))
check("前值=0 也算金叉", indicators.is_golden_cross(np.array([0.0, 0.1])))
check("持续为正不算金叉", not indicators.is_golden_cross(np.array([1.0, 2.0])))
check("持续为负不算金叉", not indicators.is_golden_cross(np.array([-2.0, -1.0])))
check("下穿不算金叉", not indicators.is_golden_cross(np.array([1.0, -0.5])))
check("长度不足 2 返回 False", not indicators.is_golden_cross(np.array([1.0])))

print("== MACD 柱口径（2026-10-18 触发条件①：柱放大，非 DIF 线）==")
check("柱走高 → True", indicators.is_macd_hist_rising(0.5, 0.2))
check("柱走低 → False", not indicators.is_macd_hist_rising(0.2, 0.5))
check("持平 → False（严格大于）", not indicators.is_macd_hist_rising(0.2, 0.2))
check("负值收窄也算放大", indicators.is_macd_hist_rising(-0.1, -0.5))
check("由负转正也算放大", indicators.is_macd_hist_rising(0.1, -0.3))
check("None → False",
      not indicators.is_macd_hist_rising(None, 0.2)
      and not indicators.is_macd_hist_rising(0.2, None))

print("== CCI(14) 口径 ==")
highs = closes + 1.0
lows = closes - 1.0
cci = indicators.cci_series(highs, lows, closes)
check("CCI 返回等长序列", len(cci) == len(closes))
check("CCI 尾部非 NaN", not np.isnan(cci[-1]))
cci_diff = indicators.cci_cross_diff(cci)
check("CCI 差分 = CCI - MA(CCI,14)",
      np.allclose(cci_diff[-1], cci[-1] - np.mean(cci[-14:]), atol=1e-9))

print("== 临时 bar 拼接（14:30 口径）==")
import pandas as pd  # noqa: E402

hist = pd.DataFrame({
    "date": pd.date_range("2026-01-01", periods=120, freq="B"),
    "open": closes[:120], "close": closes[:120],
    "high": highs[:120], "low": lows[:120], "volume": 1000,
})
states = indicators.compute_states(hist)
check("纯历史序列可计算", states["macd_cross"] is not None and states["bars"] == 120)
check("compute_states 返回 macd_hist/macd_hist_prev",
      states["macd_hist"] is not None and states["macd_hist_prev"] is not None)
check("macd_hist ≈ (dif-dea)*2（与 MyTT 柱口径一致）",
      abs(states["macd_hist"] - (states["dif"] - states["dea"]) * 2) < 0.01,
      f"hist={states['macd_hist']:.4f} "
      f"(dif-dea)*2={(states['dif'] - states['dea']) * 2:.4f}")
# 追加临时 bar：close 用当前价（大涨），应把最后一点的 DIF 推高
temp = {"close": closes[119] * 1.05, "high": closes[119] * 1.06, "low": closes[119] * 1.04}
states2 = indicators.compute_states(hist, temp_bar=temp)
check("追加临时 bar 后 bars+1", states2["bars"] == 121)
check("临时 bar 改变最后一点 DIF", states2["dif"] != states["dif"])

print("== 腾讯字段解析 ==")
# 按真实结构构造：f[0]=1 f[1]=名称 f[2]=代码 f[3]=当前价 f[4]=昨收
# f[5..29]=盘口/逐笔（25 个） f[30]=时间 f[31]=涨跌 f[32]=涨跌%
# f[33]=最高 f[34]=最低
line = ('v_sh600519="1~贵州茅台~600519~1329.00~1312.97~'
        + "~".join(str(i) for i in range(5, 30)) +
        '~20261016161447~16.03~1.22~1329.88~1301.68~35~36~37~38~39~40";')
q = market._parse_quote_line(line)
check("当前价 fields[3]", q and q["current"] == 1329.00)
check("昨收 fields[4]", q and q["prev_close"] == 1312.97)
check("最高 fields[33]", q and q["high"] == 1329.88)
check("最低 fields[34]", q and q["low"] == 1301.68)

print("== 代码转换 ==")
check("600519→sh", market.to_qt_symbol("600519") == "sh600519")
check("000001→sz", market.to_qt_symbol("000001") == "sz000001")
check("830799→bj", market.to_qt_symbol("830799") == "bj830799")
check("430089→bj", market.to_qt_symbol("430089") == "bj430089")
check("920363→bj（北交所新代码段）", market.to_qt_symbol("920363") == "bj920363")
check("688500→sh（科创板）", market.to_qt_symbol("688500") == "sh688500")
check("900901→sh（B股）", market.to_qt_symbol("900901") == "sh900901")

print()
if failures:
    print(f"失败 {failures} 项")
    sys.exit(1)
print("全部通过")
