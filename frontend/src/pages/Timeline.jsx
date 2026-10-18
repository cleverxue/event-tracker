// Timeline 页（规格第七章）：单只股票的公告时间线
// 标注每条公告属于哪个事件（event_id → 事件族）、推进了什么状态，附巨潮原文链接
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchTimeline, fetchWatchlist } from '../api.js'

export default function Timeline() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState('')
  const [symbol, setSymbol] = useState(searchParams.get('symbol') || '')
  const [items, setItems] = useState(null)
  const [eventMeta, setEventMeta] = useState({}) // event_id -> {family, status}
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const metaLoaded = useRef(false)

  // 事件归属映射：watchlist 全量拉一次，建 event_id → 事件族/状态
  useEffect(() => {
    if (metaLoaded.current) return
    metaLoaded.current = true
    fetchWatchlist()
      .then((d) => {
        const m = {}
        for (const r of d.items || []) {
          m[r.event_id] = { family: r.event_family, status: r.status }
        }
        setEventMeta(m)
      })
      .catch(() => { /* 归属映射失败不阻塞主流程，仅不显示事件族徽章 */ })
  }, [])

  const load = useCallback(async (sym) => {
    if (!sym) { setItems(null); return }
    setLoading(true)
    setError('')
    try {
      const d = await fetchTimeline(sym)
      setItems(d.items || [])
      setSearchParams({ symbol: sym }, { replace: true })
    } catch (e) {
      setError(String(e.message || e))
      setItems(null)
    } finally {
      setLoading(false)
    }
  }, [setSearchParams])

  // 从 Watchlist/Signals 跳转带入 ?symbol= 时自动加载。
  // 注意：组件新挂载时 symbol 初始 state 已等于 URL 参数，
  // 不能用 s !== symbol 判断（会永远跳过首次加载），
  // 用 ref 记录"已加载过的 symbol"才准确。
  const loadedRef = useRef('')
  useEffect(() => {
    const s = searchParams.get('symbol') || ''
    setInput(s)
    if (s && s !== loadedRef.current) {
      loadedRef.current = s
      setSymbol(s)
      load(s)
    }
  }, [searchParams, load])

  const onSubmit = (e) => {
    e.preventDefault()
    const sym = input.trim()
    if (!sym) return
    loadedRef.current = sym
    setSymbol(sym)
    load(sym)
  }

  const symEvents = items
    ? [...new Set(items.map((t) => t.event_id))].map((eid) => ({
        event_id: eid,
        ...(eventMeta[eid] || {}),
      }))
    : []

  return (
    <div>
      <h2 className="page-title">Timeline</h2>
      <p className="page-sub">单只股票的公告时间线：每条公告属于哪个事件、推进了什么状态。</p>

      <form className="toolbar" onSubmit={onSubmit}>
        <div className="field">
          <label>股票代码</label>
          <input value={input} placeholder="如 600000 / 000001"
                 onChange={(e) => setInput(e.target.value)} style={{ minWidth: 180 }} />
        </div>
        <button className="btn primary" type="submit">查询</button>
      </form>

      {error && <div className="error-box">加载失败：{error}</div>}

      {symbol && symEvents.length > 0 && (
        <div className="card">
          <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
            该股共 {symEvents.length} 个跟踪事件：
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            {symEvents.map((e) => (
              <span key={e.event_id} className="badge badge-blue" title={e.event_id}>
                {e.family || e.event_id}
              </span>
            ))}
          </div>
        </div>
      )}

      {loading && <div className="loading">加载中…</div>}

      {!loading && !error && items && (
        items.length === 0 ? (
          <div className="empty">{symbol} 暂无时间线记录</div>
        ) : (
          <div className="card">
            <ul className="timeline">
              {items.map((t) => (
                <li key={t.announcement_id || `${t.event_id}-${t.publish_timestamp}`}>
                  <div className="tl-head">
                    <span className="tl-time mono">
                      {String(t.publish_timestamp || '').slice(0, 10)}
                    </span>
                    <span className="tl-title">{t.title}</span>
                    {eventMeta[t.event_id]?.family && (
                      <span className="badge badge-blue">{eventMeta[t.event_id].family}</span>
                    )}
                    {t.state_before || t.state_after ? (
                      <span className="badge badge-amber">
                        {t.state_before || '初始'} → {t.state_after || '?'}
                      </span>
                    ) : (
                      <span className="badge badge-gray">初始事件</span>
                    )}
                    {t.source_url && (
                      <a className="tl-link" href={t.source_url} target="_blank" rel="noreferrer">原文</a>
                    )}
                  </div>
                  <div className="tl-meta">
                    {t.matched_rule ? `命中规则：${t.matched_rule}` : ''}
                    {t.event_id ? ` · event_id: ${t.event_id}` : ''}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )
      )}
    </div>
  )
}
