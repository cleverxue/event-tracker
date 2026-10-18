// Signals 页（规格第七章）：按时间倒序 + 日期筛选
// + 触发时与次日收盘时的 MACD柱/CCI(14) 原始值对比（2026-10-18 口径，
//   替代原金叉布尔对比——存原始值供人工复核，收盘时可自行判断条件是否仍成立；
//   2026-10-18 二次修订：触发条件①由 DIF 线改为 MACD 柱）
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchSignals } from '../api.js'

// 数值解析：CSV 空串/None -> null（与 0 区分）
function toNum(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function fmt(v, nd) {
  const n = toNum(v)
  return n === null ? '-' : n.toFixed(nd)
}

// 指标对比单元格：触发时值 → 收盘时值（收盘未回填显示"待回填"）
function CompareCell({ triggerVal, closeVal, nd, render }) {
  const t = toNum(triggerVal)
  const c = toNum(closeVal)
  return (
    <div className="compare-cell">
      <span>触发时：{t === null ? '-' : (render ? render(t) : fmt(t, nd))}</span>
      <span>收盘时：{
        c === null
          ? <span className="badge badge-gray">待回填</span>
          : (render ? render(c) : fmt(c, nd))
      }</span>
    </div>
  )
}

export default function Signals() {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const d = await fetchSignals({ from, to })
      setRows(d.items || [])
      setTotal(d.total || 0)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }, [from, to])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <h2 className="page-title">Signals</h2>
      <p className="page-sub">
        触发信号（时间倒序）。触发条件：偏离度&gt;1% 且 MACD柱较前一日放大 且 CCI(14)&lt;-100。
        收盘时原始值由次日 16:00 任务回填，供人工复核。共 {total} 条。
      </p>

      <div className="toolbar">
        <div className="field">
          <label>起始日期</label>
          <input type="date" value={from} max={to || undefined}
                 onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="field">
          <label>截止日期</label>
          <input type="date" value={to} min={from || undefined}
                 onChange={(e) => setTo(e.target.value)} />
        </div>
        <button className="btn" onClick={() => { setFrom(''); setTo('') }}>重置</button>
        <button className="btn primary" onClick={load}>查询</button>
      </div>

      {error && <div className="error-box">加载失败：{error}</div>}
      {loading && <div className="loading">加载中…</div>}

      {!loading && !error && (
        rows.length === 0 ? (
          <div className="empty">没有符合条件的信号</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>信号给出时间</th><th>代码</th>
                  <th className="num">触发价</th>
                  <th className="num">参照价</th><th>参照类型</th>
                  <th className="num">偏离度</th>
                  <th>MACD柱</th><th>CCI(14)</th><th>推送</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s, i) => {
                  const dev = toNum(s.deviation)
                  const devPct = dev === null ? null : dev * 100
                  const hist = toNum(s.macd_hist_at_trigger)
                  const histPrev = toNum(s.macd_hist_prev_at_trigger)
                  const rising = hist !== null && histPrev !== null && hist > histPrev
                  const cciT = toNum(s.cci_at_trigger)
                  const cciC = toNum(s.cci_at_close)
                  const oversold = cciT !== null && cciT < -100
                  const oversoldClose = cciC !== null && cciC < -100
                  return (
                    <tr key={`${s.event_id}-${s.trigger_time}-${i}`}>
                      <td className="mono" title={`给出：${s.trigger_time || '-'}`}>
                        {String(s.trigger_time || '').slice(0, 16)}
                      </td>
                      <td>
                        <Link to={`/timeline?symbol=${encodeURIComponent(s.symbol)}`}>
                          {s.symbol}
                        </Link>
                      </td>
                      <td className="num">{fmt(s.current_price, 2)}</td>
                      <td className="num">{fmt(s.ref_price_used, 2)}</td>
                      <td>{s.ref_price_type || '-'}</td>
                      <td className="num">
                        {devPct === null ? '-' : (
                          <span style={{ color: devPct > 0 ? 'var(--red)' : 'var(--green)' }}>
                            {devPct > 0 ? '+' : ''}{devPct.toFixed(2)}%
                          </span>
                        )}
                      </td>
                      <td>
                        <CompareCell
                          triggerVal={s.macd_hist_at_trigger}
                          closeVal={s.macd_hist_at_close}
                          nd={4}
                          render={(v) => (
                            <span className="mono">
                              {v.toFixed(4)}
                              {v === hist && (
                                <em style={{ marginLeft: 4, fontStyle: 'normal',
                                             color: rising ? 'var(--red)' : 'var(--green)' }}>
                                  {rising ? '↑放大' : '↓收敛'}
                                </em>
                              )}
                            </span>
                          )}
                        />
                        {hist !== null && histPrev !== null && (
                          <div className="cell-sub">前值 {histPrev.toFixed(4)}</div>
                        )}
                      </td>
                      <td>
                        <CompareCell
                          triggerVal={s.cci_at_trigger}
                          closeVal={s.cci_at_close}
                          nd={1}
                          render={(v) => (
                            <span className="mono" style={{
                              color: v < -100 ? 'var(--red)' : undefined }}>
                              {v.toFixed(1)}{v < -100 ? ' 超卖' : ''}
                            </span>
                          )}
                        />
                        {oversold && !oversoldClose && cciC !== null && (
                          <div className="cell-sub">收盘时脱离超卖区</div>
                        )}
                      </td>
                      <td>
                        {s.notified === true || s.notified === 'True'
                          ? <span className="badge badge-blue"
                                  title={`推送时刻：${s.notified_time || '-'}`}>
                              已推送{s.notified_time ? ` ${String(s.notified_time).slice(11, 19)}` : ''}
                            </span>
                          : <span className="badge badge-gray">未推送</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}
