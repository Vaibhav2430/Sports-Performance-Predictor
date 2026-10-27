import { useState, useEffect } from 'react'
import axios from '../api'

const STATS = [
  { key: 'PTS', label: 'Points',   color: '#17c3ff' },
  { key: 'AST', label: 'Assists',  color: '#2ecc71' },
  { key: 'REB', label: 'Rebounds', color: '#ff7a1a' },
]

function pctColor(pct) {
  if (pct == null) return 'var(--muted)'
  if (pct >= 60) return 'var(--green)'
  if (pct >= 50) return 'var(--orange)'
  return '#ff8a8a'
}

export default function AccuracyCards({ league }) {
  const [stats, setStats] = useState(null)
  const [stale, setStale] = useState(false)

  useEffect(() => {
    let active = true
    let pending = false
    const controller = new AbortController()
    setStats(null)
    setStale(false)
    async function refresh() {
      if (pending || document.visibilityState === 'hidden') return
      pending = true
      try {
        const { data } = await axios.get('/accuracy', {
          params: { league, metric: 'over_under' },
          signal: controller.signal, timeout: 15000,
        })
        if (active) { setStats(data); setStale(false) }
      } catch {
        if (active) setStale(true)
      } finally {
        pending = false
      }
    }
    refresh()
    const timer = setInterval(refresh, 60000)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      active = false
      controller.abort()
      clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [league])

  const hasData = stats && stats.total > 0

  return (
    <div className="accuracy-section">
      <div className="accuracy-title">O/U Accuracy</div>

      <div className="accuracy-cards">
        {STATS.map(({ key, label, color }) => {
          const s = hasData ? stats.by_stat[key] : null
          const hasStatData = s && s.total > 0
          return (
            <div key={key} className="accuracy-card" title={(hasStatData
              ? `${label}: ${s.correct}/${s.total} model over/under picks won against saved DraftKings lines.`
              : `${label} over/under accuracy`) + (stale ? ' Showing saved results; refresh temporarily unavailable.' : '')}>
              <div className="accuracy-card-label" style={{ color }}>{label}</div>
              <div
                className="accuracy-card-pct"
                style={{ color: hasStatData ? pctColor(s.pct) : 'var(--border2)' }}
              >
                {hasStatData ? `${s.pct}%` : '—'}
              </div>
              <div className="accuracy-card-sub">
                {hasStatData ? `${s.correct}/${s.total}` : 'no data'}
              </div>
              <div className="accuracy-card-bar-track">
                <div
                  className="accuracy-card-bar-fill"
                  style={{
                    width: hasStatData ? `${s.pct}%` : '0%',
                    background: hasStatData ? pctColor(s.pct) : 'var(--border2)',
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>

      {stats?.pending_count > 0 && (
        <div className="accuracy-pending-note">{stats.pending_count} pending</div>
      )}
    </div>
  )
}
