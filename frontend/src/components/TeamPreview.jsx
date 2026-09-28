import { useEffect, useState } from 'react'
import axios from 'axios'
import { teamLogoUrl, playerHeadshotUrl } from '../teamLogo'

function rankClass(rank, isWNBA) {
  if (!rank) return 'rank-mid'
  const total = isWNBA ? 15 : 30
  const third = total / 3
  if (rank <= third) return 'rank-good'
  if (rank > third * 2) return 'rank-bad'
  return 'rank-mid'
}

function TeamColumn({ team, isWNBA, onSelectPlayer }) {
  if (!team) return null
  const logo = teamLogoUrl(team.tricode, isWNBA)

  return (
    <div className="tp-team">
      <div className="tp-team-header">
        <div className="tp-team-logo">
          {logo
            ? <img src={logo} alt={team.tricode} onError={e => { e.target.style.visibility = 'hidden' }} />
            : '🏀'}
        </div>
        <div>
          <div className="tp-team-name">{team.team_name}</div>
          <div className="tp-team-ranks">
            {team.off_rank != null && (
              <span className={`rank-badge ${rankClass(team.off_rank, isWNBA)}`}>
                OFF #{team.off_rank} · {team.off_rtg?.toFixed(1)}
              </span>
            )}
            {team.def_rank != null && (
              <span className={`rank-badge ${rankClass(team.def_rank, isWNBA)}`}>
                DEF #{team.def_rank} · {team.def_rtg?.toFixed(1)}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="tp-roster">
        {team.players?.length > 0 ? (
          team.players.map(p => {
            const headshot = playerHeadshotUrl(p.id, isWNBA)
            return (
              <button
                type="button"
                className="tp-player"
                key={p.id ?? p.name}
                onClick={() => onSelectPlayer(p.name)}
                aria-label={`View predictions for ${p.name}`}
                title={`View predictions for ${p.name}`}
              >
                <span className="tp-player-headshot">
                  {headshot
                    ? <img src={headshot} alt="" onError={e => { e.target.style.visibility = 'hidden' }} />
                    : <span>🏀</span>}
                </span>
                <span className="tp-player-info">
                  <span className="tp-player-name">{p.name}</span>
                  <span className="tp-player-min">{p.min} MIN</span>
                </span>
                <span className="tp-player-stats">
                  <span className="tp-stat"><span className="tp-stat-val td-pts">{p.pts}</span><span className="tp-stat-lbl">PTS</span></span>
                  <span className="tp-stat"><span className="tp-stat-val td-ast">{p.ast}</span><span className="tp-stat-lbl">AST</span></span>
                  <span className="tp-stat"><span className="tp-stat-val td-reb">{p.reb}</span><span className="tp-stat-lbl">REB</span></span>
                </span>
              </button>
            )
          })
        ) : (
          <div className="tp-roster-empty">No roster data available.</div>
        )}
      </div>
    </div>
  )
}

export default function TeamPreview({ game, league, onClose, onSelectPlayer }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const isWNBA = league === 'WNBA'

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setPreview(null)

    axios.get('/team_preview', { params: { home: game.home.tricode, away: game.away.tricode, league } })
      .then(res => { if (!cancelled) setPreview(res.data) })
      .catch(() => { if (!cancelled) setError('Could not load team preview.') })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [game.gameId, league])

  return (
    <div className="team-preview fade-in">
      <div className="tp-header">
        <div className="tp-title">{game.away.tricode} @ {game.home.tricode}</div>
        <button type="button" className="tp-close" onClick={onClose} aria-label="Close game preview and return to player search" title="Back to player search">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      {loading && (
        <div className="loading">
          <div className="spinner" />
          <span>Loading team preview…</span>
        </div>
      )}

      {error && <div className="error">⚠ {error}</div>}

      {preview && !loading && (
        <div className="tp-columns">
          <TeamColumn team={preview.away} isWNBA={isWNBA} onSelectPlayer={onSelectPlayer} />
          <div className="tp-divider" />
          <TeamColumn team={preview.home} isWNBA={isWNBA} onSelectPlayer={onSelectPlayer} />
        </div>
      )}
    </div>
  )
}
