import { useState, useRef } from 'react'
import axios from 'axios'
import GamesSidebar from './components/GamesSidebar'
import AccuracyCards from './components/AccuracyCards'
import PlayerSearch from './components/PlayerSearch'
import PropCards from './components/PropCards'
import StatChart from './components/StatChart'
import GameLogTable from './components/GameLogTable'
import TeamPreview from './components/TeamPreview'
import Chatbot from './components/Chatbot'
import HomeExplore from './components/HomeExplore'
import { teamLogoUrl as teamLogoUrlFor } from './teamLogo'

export default function App() {
  const predictionRequest = useRef(0)
  const [league, setLeague]     = useState('NBA')   // 'NBA' | 'WNBA'
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [selectedGame, setSelectedGame] = useState(null)

  function switchLeague(l) {
    if (l === league) return
    predictionRequest.current += 1
    setLoading(false)
    setLeague(l)
    setData(null)
    setError(null)
    setSelectedGame(null)
  }

  async function handleSearch(playerName) {
    const requestId = ++predictionRequest.current
    setSelectedGame(null)
    setLoading(true)
    setError(null)
    setData(null)
    const endpoint = league === 'WNBA' ? '/wnba/predict' : '/predict'
    try {
      const res = await axios.get(endpoint, { params: { player: playerName } })
      if (requestId === predictionRequest.current) setData(res.data)
    } catch (err) {
      if (requestId === predictionRequest.current) setError(err.response?.data?.detail ?? 'The prediction service could not complete this request. Please retry shortly.')
    } finally {
      if (requestId === predictionRequest.current) setLoading(false)
    }
  }

  function handleSelectGame(game) {
    setData(null)
    setError(null)
    setSelectedGame(prev => (prev?.gameId === game.gameId ? null : game))
  }

  const hasResults = data && !loading
  const isWNBA     = league === 'WNBA'

  function teamLogoUrl() {
    if (!data) return null
    const abbr = isWNBA ? data.team_abbr : data.team
    return teamLogoUrlFor(abbr, isWNBA)
  }

  function rankClass(rank) {
    if (!rank) return 'rank-mid'
    const total = isWNBA ? 15 : 30
    const third = total / 3
    if (rank <= third) return 'rank-good'
    if (rank > third * 2) return 'rank-bad'
    return 'rank-mid'
  }

  return (
    <>
      <header className="header">
        <div className="logo">
          <span className="brand-mark" aria-hidden="true"><span>C</span><span>C</span></span>
          CourtCast
        </div>

        <AccuracyCards league={league} />

        {/* League toggle */}
        <div className="league-toggle">
          <button
            className={`league-btn wnba ${league === 'NBA' ? 'active' : ''}`}
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
        <GamesSidebar league={league} selectedGameId={selectedGame?.gameId} onSelectGame={handleSelectGame} />

        <main className={`main ${!hasResults && !selectedGame ? 'home-main' : ''}`}>
          <div className={`search-section ${selectedGame ? 'wide' : ''}`}>
            {selectedGame ? (
              <TeamPreview
                game={selectedGame}
                league={league}
                onSelectPlayer={handleSearch}
                onClose={() => setSelectedGame(null)}
              />
            ) : (
              <>
                {!hasResults && (
                  <div className="search-hero">
                    <h1>Every player. Every angle.<br /><span>Your next insight.</span></h1>
                    <p>Explore next-game projections, recent form, and the numbers behind the matchup.</p>
                  </div>
                )}
                <PlayerSearch key={league} onSearch={handleSearch} loading={loading} league={league} />
                {error && <div className="error">⚠ {error}</div>}
              </>
            )}
          </div>

          {!hasResults && !selectedGame && !loading && <HomeExplore league={league} onSelectPlayer={handleSearch} />}

          {loading && (
            <div className="loading">
              <div className="spinner" />
              <span>
                {`Fetching ${league} game log & training model…`}
              </span>
            </div>
          )}

          {hasResults && (
            <div className="results fade-in">
              <div className="player-banner">
                <div className="player-left">
                  <div className="player-avi" style={{ background: 'linear-gradient(135deg,#b83bff,#ff2d95)' }}>
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
                    </div>
                  </div>
                </div>
              </div>

              {data.warnings?.length > 0 && <div className="prediction-note" role="status">
                {data.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
              </div>}

              {data.injury && (() => {
                const inj = data.injury
                const isOut = inj.status.toLowerCase() === 'out'
                return (
                  <div className={`injury-banner ${isOut ? 'out' : 'limited'}`}>
                    <span className="injury-icon">{isOut ? '🚫' : '⚠️'}</span>
                    <div>
                      <span className="injury-status">{inj.status}</span>
                      {inj.injury_type && <span className="injury-type">· {inj.injury_type}</span>}
                      {inj.comment && <div style={{ marginTop: 4, opacity: 0.85 }}>{inj.comment}</div>}
                    </div>
                  </div>
                )
              })()}

              <div className="section-label">Next Game Projections</div>
              <PropCards predictions={data.predictions} gameLog={data.game_log} league={league} lines={data.lines ?? {}} />

              {data.game_log.length > 0 ? <>
              <div className="section-label">Performance Trend</div>
              <StatChart gameLog={data.game_log} predictions={data.predictions} />

              <div className="section-label">Game Log</div>
              <GameLogTable gameLog={data.game_log} predictions={data.predictions} league={league} />
              </> : <div className="prediction-note">No NBA game history available yet. Recent averages and performance charts will appear after the player has played.</div>}
            </div>
          )}
        </main>
      </div>

      <Chatbot key={league} league={league} currentData={data} />
    </>
  )
}
