// Watchlist 页（规格第七章）：表格 + 状态/事件族筛选 + 点击行展开 timeline
import { useCallback, useEffect, useRef, useState, Fragment } from 'react'
import { fetchWatchlist, fetchTimeline } from '../api.js'

const STATUS_OPTIONS = ['watching', 'triggered', 'completed', 'expired']
const FAMILY_OPTIONS = ['回购', '增持', '协议转让']
const PAGE_SIZE = 100

const STATUS_BADGE = {
  watching: ['badge-blue', '跟踪中'],
  triggered: ['badge-red', '已触发'],
  completed: ['badge-gray', '已完成'],
  expired: ['badge-gray', '已过期'],
}

function fmt(v) {
  if (v === null || v === undefined || v === '') return '-'
  return v
}

function fmtPrice(v) {
  if (v === null || v === undefined || v === '') return '-'
  return Number(v).toFixed(3)
}

export default function Watchlist() {
  const [status, setStatus] = useState('')
  const [family, setFamily] = useState('')
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 展开行：symbol -> timeline 数据缓存（避免重复请求）
  const [expanded, setExpanded] = useState('') // 当前展开的 symbol
  const tlCache = useRef(new Map())
  const [tlLoading, setTlLoading] = useState(false)
  const [tlError, setTlError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const d = await fetchWatchlist({ status, family })
      setRows(d.items || [])
      setTotal(d.total || 0)
      setPage(0)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }, [status, family])

  useEffect(() => { load() }, [load])

  const onToggleRow = async (symbol) => {
    if (expanded === symbol) { setExpanded(''); return }
    setExpanded(symbol)
    setTlError('')
    if (tlCache.current.has(symbol)) return
    setTlLoading(true)
    try {
      const d = await fetchTimeline(symbol)
      tlCache.current.set(symbol, d.items || [])
    } catch (e) {
      setTlError(String(e.message || e))
    } finally {
      setTlLoading(false)
    }
  }

  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))

  return (
    <div>
      <h2 className="page-title">Watchlist</h2>
      <p className="page-sub">
        公告事件跟踪池（回购 / 增持 / 协议转让）。共 {total} 条，点击行展开该股的公告时间线。
      </p>

      <div className="toolbar">
        <div className="field">
          <label>状态</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{STATUS_BADGE[s] ? STATUS_BADGE[s][1] : s}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>事件族</label>
          <select value={family} onChange={(e) => setFamily(e.target.value)}>
            <option value="">全部</option>
            {FAMILY_OPTIONS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
        <button className="btn" onClick={() => { setStatus(''); setFamily('') }}>重置</button>
      </div>

      {error && <div className="error-box">加载失败：{error}</div>}
      {loading && <div className="loading">加载中…</div>}

      {!loading && !error && (
        rows.length === 0 ? (
          <div className="empty">没有符合条件的记录</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>代码</th><th>事件族</th><th>状态</th><th>价格类型</th>
                    <th className="num">参照价</th><th className="num">成交均价</th>
                    <th>跟踪截止</th><th>进入自选</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((r) => (
                    <Fragment key={r.event_id}>
                      <tr className="row-clickable" onClick={() => onToggleRow(r.symbol)}>
                        <td className="mono">{r.symbol}</td>
                        <td><span className="badge badge-blue">{r.event_family}</span></td>
                        <td>
                          <span className={`badge ${STATUS_BADGE[r.status]?.[0] || 'badge-gray'}`}>
                            {STATUS_BADGE[r.status]?.[1] || r.status}
                          </span>
                        </td>
                        <td>{fmt(r.price_type)}</td>
                        <td className="num">{fmtPrice(r.reference_price)}</td>
                        <td className="num">{fmtPrice(r.avg_price)}</td>
                        <td className="mono">{fmt(r.tracking_expiry)}</td>
                        <td className="mono" title={fmt(r.entry_basis)}>
                          {fmt(r.added_time) === '-' ? '—（存量）' : String(r.added_time).slice(0, 16)}
                        </td>
                      </tr>
                      {expanded === r.symbol && (
                        <tr className="expand-row">
                          <td colSpan={8}>
                            {r.entry_basis && (
                              <div className="entry-basis">进入依据：{r.entry_basis}</div>
                            )}
                            <TimelinePanel
                              symbol={r.symbol}
                              currentEventId={r.event_id}
                              items={tlCache.current.get(r.symbol)}
                              loading={tlLoading}
                              error={tlError}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
              <span style={{ fontSize: 13, color: 'var(--text-2)' }}>
                第 {page + 1} / {pageCount} 页（每页 {PAGE_SIZE} 条）
              </span>
              <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
              <button className="btn" disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>下一页</button>
            </div>
          </>
        )
      )}
    </div>
  )
}

// 展开面板：该 symbol 的公告时间线（当前事件高亮标注）
function TimelinePanel({ symbol, currentEventId, items, loading, error }) {
  if (loading) return <div className="loading">时间线加载中…</div>
  if (error) return <div className="error-box">时间线加载失败：{error}</div>
  if (!items || items.length === 0) return <div className="empty">该股暂无时间线记录</div>
  return (
    <div className="expand-panel">
      <h4>{symbol} 公告时间线（{items.length} 条）</h4>
      <ul>
        {items.map((t) => (
          <li key={t.announcement_id || `${t.event_id}-${t.publish_timestamp}`}>
            <span className="tl-time">{String(t.publish_timestamp || '').slice(0, 10)}</span>
            <span className="tl-title">{t.title}</span>
            {t.event_id === currentEventId && <span className="badge badge-amber">本事件</span>}
            {t.state_before || t.state_after ? (
              <span className="badge badge-gray">
                {fmt(t.state_before)} → {fmt(t.state_after)}
              </span>
            ) : null}
            {t.matched_rule && <span className="badge badge-blue">{t.matched_rule}</span>}
            {t.source_url && (
              <a className="tl-link" href={t.source_url} target="_blank" rel="noreferrer"
                 onClick={(e) => e.stopPropagation()}>原文</a>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
