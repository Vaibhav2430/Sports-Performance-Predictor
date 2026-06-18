import { useState } from 'react'
import axios from 'axios'
import GamesSidebar from './components/GamesSidebar'
import PlayerSearch from './components/PlayerSearch'
import PropCards from './components/PropCards'
import StatChart from './components/StatChart'
import GameLogTable from './components/GameLogTable'

export default function App() {
  const [league, setLeague]   = useState('NBA')   // 'NBA' | 'WNBA'
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  function switchLeague(l) {
    if (l === league) return
    setLeague(l)
    setData(null)
    setError(null)
  }

  async function handleSearch(playerName) {
    setLoading(true)
    setError(null)
    setData(null)
    const endpoint = league === 'WNBA' ? '/wnba/predict' : '/predict'
    try {
      const res = await axios.get(endpoint, { params: { player: playerName } })
      setData(res.data)
    } catch (err) {
      setError(err.response?.data?.detail ?? 'Player not found or no data available.')
    } finally {
      setLoading(false)
    }
  }

  const isHot = data
    ? data.predictions.PTS.last5_avg > data.predictions.PTS.season_avg
    : false

  const hasResults = data && !loading
  const isWNBA     = league === 'WNBA'

  const NBA_ESPN_MAP = { GSW: 'gs', SAS: 'sa', NYK: 'ny', NOP: 'no', UTA: 'utah' }
  function teamLogoUrl() {
    if (!data) return null
    const abbr = isWNBA ? data.team_abbr : data.team
    if (!abbr) return null
    if (isWNBA) return `https://a.espncdn.com/i/teamlogos/wnba/500/${abbr.toLowerCase()}.png`
    const espn = NBA_ESPN_MAP[abbr] ?? abbr.toLowerCase()
    return `https://a.espncdn.com/i/teamlogos/nba/500/${espn}.png`
  }

  function rankClass(rank) {
    if (!rank) return 'rank-mid'
    if (rank <= 10) return 'rank-good'
    if (rank >= 21) return 'rank-bad'
    return 'rank-mid'
  }

  return (
    <>
      <header className="header">
        <div className="logo">
          <div className="logo-icon">🏀</div>
          AI <span>Predicts</span>
        </div>

        {/* League toggle */}
        <div className="league-toggle">
          <button
            className={`league-btn ${league === 'NBA' ? 'active' : ''}`}
            onClick={() => switchLeague('NBA')}
          >
            NBA
          </button>
          <button
            className={`league-btn wnba ${league === 'WNBA' ? 'active' : ''}`}
            onClick={() => switchLeague('WNBA')}
          >
            WNBA
          </button>
        </div>
      </header>

      <div className="shell">
        <GamesSidebar league={league} />

        <main className={`main ${hasResults ? '' : 'empty'}`}>
          <div className="search-section">
            {!hasResults && (
              <div className="search-hero">
                <h1>{isWNBA ? 'WNBA' : 'NBA'} Stat Predictor</h1>
                <p>AI projections for points, assists &amp; rebounds — next game.</p>
              </div>
            )}
            <PlayerSearch onSearch={handleSearch} loading={loading} league={league} />
            {error && <div className="error">⚠ {error}</div>}
          </div>

          {loading && (
            <div className="loading">
              <div className="spinner" />
              <span>
                {isWNBA
                  ? 'Fetching WNBA game log & training model… (~15s)'
                  : 'Fetching game log & training model… (~10s)'}
              </span>
            </div>
          )}

          {hasResults && (
            <div className="results fade-in">
              <div className="player-banner">
                <div className="player-left">
                  <div className="player-avi" style={isWNBA ? { background: 'linear-gradient(135deg,#a855f7,#ec4899)' } : {}}>
                    {teamLogoUrl()
                      ? <img src={teamLogoUrl()} alt={data.team} style={{ width: '85%', height: '85%', objectFit: 'contain' }} />
                      : '🏀'}
                  </div>
                  <div>
                    <div className="player-name">{data.player}</div>
                    <div className="player-sub">
                      {data.team_name ?? data.team}
                      {data.team_off_rank != null && (
                        <span className={`rank-badge ${rankClass(data.team_off_rank)}`}>OFF #{data.team_off_rank}</span>
                      )}
                      {data.team_def_rank != null && (
                        <span className={`rank-badge ${rankClass(data.team_def_rank)}`}>DEF #{data.team_def_rank}</span>
                      )}
                      {' · '}{data.season} · {data.games_used} games analyzed
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 7 }}>
                  {isHot && <span className="badge badge-orange">🔥 Hot Streak</span>}
                  <span className="badge" style={isWNBA
                    ? { background: 'rgba(168,85,247,0.12)', color: '#c084fc', border: '1px solid rgba(168,85,247,0.25)' }
                    : { background: 'rgba(34,197,94,0.1)', color: 'var(--green)', border: '1px solid rgba(34,197,94,0.22)' }
                  }>
                    {isWNBA ? '♀ WNBA' : '✓ NBA'} · AI Prediction
                  </span>
                </div>
              </div>

              <div className="section-label">Next Game Projections</div>
              <PropCards predictions={data.predictions} gameLog={data.game_log} league={league} />

              <div className="section-label">Performance Trend</div>
              <StatChart gameLog={data.game_log} predictions={data.predictions} />

              <div className="section-label">Game Log</div>
              <GameLogTable gameLog={data.game_log} predictions={data.predictions} league={league} />
            </div>
          )}
        </main>
      </div>
    </>
  )
}
