// API 封装：与后端只读接口一一对应（规格第七章）
// GET /api/watchlist?status=&family=    列表筛选
// GET /api/watchlist/{symbol}/timeline  事件时间线
// GET /api/signals?from=&to=            历史信号

async function getJSON(url) {
  const resp = await fetch(url)
  if (!resp.ok) {
    let detail = ''
    try {
      detail = JSON.stringify(await resp.json())
    } catch {
      detail = await resp.text().catch(() => '')
    }
    throw new Error(`请求失败 ${resp.status}: ${detail || url}`)
  }
  return resp.json()
}

export function fetchWatchlist({ status, family } = {}) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (family) params.set('family', family)
  const qs = params.toString()
  return getJSON(`/api/watchlist${qs ? `?${qs}` : ''}`)
}

export function fetchTimeline(symbol) {
  return getJSON(`/api/watchlist/${encodeURIComponent(symbol)}/timeline`)
}

export function fetchSignals({ from, to } = {}) {
  const params = new URLSearchParams()
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  const qs = params.toString()
  return getJSON(`/api/signals${qs ? `?${qs}` : ''}`)
}
