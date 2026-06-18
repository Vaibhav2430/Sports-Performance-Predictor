import { useEffect, useState } from 'react'
import axios from 'axios'

const NBA_ESPN_MAP = { GSW: 'gs', SAS: 'sa', NYK: 'ny', NOP: 'no', UTA: 'utah' }

function teamLogo(tricode, isWNBA) {
  if (!tricode) return null
  if (isWNBA) return `https://a.espncdn.com/i/teamlogos/wnba/500/${tricode.toLowerCase()}.png`
  const espn = NBA_ESPN_MAP[tricode] ?? tricode.toLowerCase()
  return `https://a.espncdn.com/i/teamlogos/nba/500/${espn}.png`
}

export default function GamesSidebar({ league }) {
  const [games, setGames]     = useState([])
  const [loading, setLoading] = useState(true)

  async function fetchGames() {
    setLoading(true)
    try {
      const endpoint = league === 'WNBA' ? '/wnba/games/today' : '/games/today'
      const res = await axios.get(endpoint)
      setGames(res.data)
    } catch {
      setGames([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchGames()
    const interval = setInterval(fetchGames, 30000)
    return () => clearInterval(interval)
  }, [league])

  const hasLive = games.some(g => g.statusCode === 2)

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-title">
          {hasLive && <div className="live-dot" />}
          {league === 'WNBA' ? 'WNBA' : 'NBA'} · Today's Games
        </div>
      </div>

      {loading && <div className="sidebar-spinner" />}

      {!loading && games.length === 0 && (
        <div className="no-games">
          No games scheduled today.
        </div>
      )}

      {games.map(g => {
        const isLive    = g.statusCode === 2
        const isFinal   = g.statusCode === 3
        const statusCls = isLive ? 'live' : isFinal ? 'final' : 'sched'
        const isWNBA    = league === 'WNBA'

        return (
          <div key={g.gameId} className={`game-card ${statusCls}`}>
            <div className="game-teams">
              {/* Away */}
              <div className="game-team">
                <img
                  src={teamLogo(g.away.tricode, isWNBA)}
                  alt={g.away.tricode}
                  className="sidebar-team-logo"
                  onError={e => { e.target.style.visibility = 'hidden' }}
                />
                <div className="team-tricode">{g.away.tricode}</div>
                <div className="team-record">{g.away.wins}–{g.away.losses}</div>
              </div>

              {/* Score or vs */}
              <div className="game-score">
                {(isLive || isFinal) ? (
                  <>
                    <span style={{ color: g.away.score > g.home.score ? 'var(--text)' : 'var(--muted)' }}>
                      {g.away.score}
                    </span>
                    <span className="score-sep">–</span>
                    <span style={{ color: g.home.score > g.away.score ? 'var(--text)' : 'var(--muted)' }}>
                      {g.home.score}
                    </span>
                  </>
                ) : (
                  <span style={{ fontSize: '0.75rem', color: 'var(--muted)', fontWeight: 500 }}>vs</span>
                )}
              </div>

              {/* Home */}
              <div className="game-team">
                <img
                  src={teamLogo(g.home.tricode, isWNBA)}
                  alt={g.home.tricode}
                  className="sidebar-team-logo"
                  onError={e => { e.target.style.visibility = 'hidden' }}
                />
                <div className="team-tricode">{g.home.tricode}</div>
                <div className="team-record">{g.home.wins}–{g.home.losses}</div>
              </div>
            </div>

            <div className={`game-status ${statusCls}`}>
              {isLive ? `🔴 ${g.status}` : g.status}
            </div>
          </div>
        )
      })}
    </aside>
  )
}
