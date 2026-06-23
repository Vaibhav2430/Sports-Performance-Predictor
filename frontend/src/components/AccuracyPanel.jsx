import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

const STAT_LABELS = { PTS: 'Points', AST: 'Assists', REB: 'Rebounds' }

export default function AccuracyPanel() {
  const [open, setOpen]       = useState(false)
  const [stats, setStats]     = useState(null)
  const [loading, setLoading] = useState(false)
  const ref = useRef(null)

  async function load() {
    setLoading(true)
    try {
      const res = await axios.get('/accuracy')
      setStats(res.data)
    } catch {
      setStats(null)
    } finally {
      setLoading(false)
    }
  }

  function toggle() {
    if (!open && !stats) load()
    setOpen(o => !o)
  }

  // Close on outside click
  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const pct = stats?.pct

  return (
    <div className="acc-wrap" ref={ref}>
      <button className="acc-trigger" onClick={toggle}>
        <span className="acc-trigger-icon">🎯</span>
        {pct != null ? `${pct}%` : 'Accuracy'}
      </button>

      {open && (
        <div className="acc-panel">
          <div className="acc-panel-title">O/U Accuracy</div>

          {loading && <div className="acc-spinner" />}

          {!loading && stats && stats.total === 0 && (
            <div className="acc-empty">
              No resolved predictions yet.<br />
              Check back after today's games finish.
            </div>
          )}

          {!loading && stats && stats.total > 0 && (
            <>
              <div className="acc-overall">
                <span className="acc-big-pct" style={{ color: pctColor(pct) }}>{pct}%</span>
                <span className="acc-overall-sub">{stats.correct}/{stats.total} correct</span>
              </div>

              <div className="acc-stat-rows">
                {['PTS', 'AST', 'REB'].map(stat => {
                  const s = stats.by_stat[stat]
                  if (!s || s.total === 0) return null
                  return (
                    <div key={stat} className="acc-stat-row">
                      <span className="acc-stat-name">{STAT_LABELS[stat]}</span>
                      <div className="acc-bar-wrap">
                        <div
                          className="acc-bar"
                          style={{ width: `${s.pct ?? 0}%`, background: pctColor(s.pct) }}
                        />
                      </div>
                      <span className="acc-stat-pct" style={{ color: pctColor(s.pct) }}>
                        {s.pct ?? '—'}%
                      </span>
                      <span className="acc-stat-n">({s.correct}/{s.total})</span>
                    </div>
                  )
                })}
              </div>

              {stats.pending_count > 0 && (
                <div className="acc-pending">
                  {stats.pending_count} pending — resolves after game finishes
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function pctColor(pct) {
  if (pct == null) return 'var(--muted)'
  if (pct >= 60) return 'var(--green)'
  if (pct >= 50) return 'var(--orange)'
  return '#f87171'
}
