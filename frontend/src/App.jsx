import { useState, useRef, useEffect } from 'react'
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
import PlayerAvatar from './components/PlayerAvatar'
import { teamLogoUrl as teamLogoUrlFor } from './teamLogo'

export default function App() {
  const predictionRequest = useRef(0)
  const [league, setLeague]     = useState('NBA')   // 'NBA' | 'WNBA'
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [selectedGame, setSelectedGame] = useState(null)

  const ratingTeam = league === 'NBA' ? data?.team : data?.team_abbr
  const ratingPlayer = data?.player

  useEffect(() => {
    if (!ratingTeam || !ratingPlayer) return
    let active = true
    let pending = false
    async function refreshRatings() {
      if (pending || document.visibilityState === 'hidden') return
      pending = true
      try {
        const { data: ratings } = await axios.get('/team_ratings', {
          params: { team: ratingTeam, league }, timeout: 15000,
        })
        if (active) setData(previous => previous?.player === ratingPlayer ? { ...previous, ...ratings } : previous)
      } catch {
        if (active) setData(previous => previous?.player === ratingPlayer ? { ...previous, ratings_stale: true } : previous)
      } finally {
        pending = false
      }
    }
    refreshRatings()
    const interval = setInterval(refreshRatings, 60000)
    window.addEventListener('focus', refreshRatings)
    document.addEventListener('visibilitychange', refreshRatings)
    return () => {
      active = false
      clearInterval(interval)
      window.removeEventListener('focus', refreshRatings)
      document.removeEventListener('visibilitychange', refreshRatings)
    }
  }, [league, ratingTeam, ratingPlayer])

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
    if (!rank) return 'rank-unknown'
    const total = data?.ratings_team_count ?? (isWNBA ? 15 : 30)
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

        <main className={`main ${selectedGame ? 'lineup-main' : !hasResults ? 'home-main' : ''}`}>
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
                  <PlayerAvatar key={`${league}-${data.player}`} name={data.player}
                    headshotUrl={data.headshot_url} fallbackUrl={teamLogoUrl()} />
                  <div>
                    <div className="player-name">{data.player}</div>
                    <div className="player-sub">
                      {data.team_name ?? data.team}
                      <span className="player-rating-badges">
                        {[['OFF', 'Offensive', data.team_off_rating, data.team_off_rank],
                          ['DEF', 'Defensive', data.team_def_rating, data.team_def_rank]].filter(([, , rating, rank]) => rating != null && rank != null).map(([label, full, rating, rank]) => (
                          <span key={label} className={`rank-badge ${rankClass(rank)}`}
                            title={`${full} team rating: ${rating ?? 'unavailable'} points per 100 possessions. Rank ${rank ?? 'unavailable'} of ${data.ratings_team_count ?? (isWNBA ? 15 : 30)}.${data.ratings_season ? ` Season ${data.ratings_season}.` : ''}${data.ratings_last_updated ? ` Updated ${new Date(data.ratings_last_updated).toLocaleString()}.` : ''}${data.ratings_stale ? ' Live feed unavailable; showing last saved ratings.' : ''}${!isWNBA && data.ratings_team_count < 30 ? ' Ranks and color tiers compare teams with available current-season ratings.' : ''}`}>
                            {label} {rating != null ? Number(rating).toFixed(1) : '—'}{rank != null ? ` · #${rank}` : ''}
                          </span>
                        ))}
                        {data.ratings_stale && <span className="ratings-stale-note">Saved ratings · refreshing</span>}
                      </span>
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
              {data.lines_last_updated && <p className="line-cache-note">Saved DraftKings lines · as of {new Date(data.lines_last_updated).toLocaleString()}</p>}
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
