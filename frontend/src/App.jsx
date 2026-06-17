import { useState } from 'react'
import axios from 'axios'
import GamesSidebar from './components/GamesSidebar'
import PlayerSearch from './components/PlayerSearch'
import PropCards from './components/PropCards'
import StatChart from './components/StatChart'
import GameLogTable from './components/GameLogTable'

export default function App() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  async function handleSearch(playerName) {
    setLoading(true)
    setError(null)
    setData(null)
    try {
      const res = await axios.get('/predict', { params: { player: playerName } })
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

  return (
    <>
      {/* Fixed header — logo only */}
      <header className="header">
        <div className="logo">
          <div className="logo-icon">🏀</div>
          Props<span>AI</span>
        </div>
      </header>

      <div className="shell">
        {/* Left sidebar — today's games */}
        <GamesSidebar />

        {/* Main content */}
        <main className={`main ${hasResults ? '' : 'empty'}`}>

          {/* Search section */}
          <div className="search-section">
            {!hasResults && (
              <div className="search-hero">
                <h1>NBA Stat Predictor</h1>
                <p>AI projections for points, assists &amp; rebounds — next game.</p>
              </div>
            )}
            <PlayerSearch onSearch={handleSearch} loading={loading} />
            {error && <div className="error">⚠ {error}</div>}
          </div>

          {/* Loading */}
          {loading && (
            <div className="loading">
              <div className="spinner" />
              <span>Fetching game log &amp; training model… (~10s)</span>
            </div>
          )}

          {/* Results */}
          {hasResults && (
            <div className="results fade-in">
              {/* Player banner */}
              <div className="player-banner">
                <div className="player-left">
                  <div className="player-avi">🏀</div>
                  <div>
                    <div className="player-name">{data.player}</div>
                    <div className="player-sub">
                      {data.season} · {data.games_used} games analyzed
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 7 }}>
                  {isHot && <span className="badge badge-orange">🔥 Hot Streak</span>}
                  <span className="badge badge-green">✓ AI Prediction</span>
                </div>
              </div>

              <div className="section-label">Next Game Projections</div>
              <PropCards predictions={data.predictions} gameLog={data.game_log} />

              <div className="section-label">Performance Trend</div>
              <StatChart gameLog={data.game_log} predictions={data.predictions} />

              <div className="section-label">Game Log</div>
              <GameLogTable gameLog={data.game_log} predictions={data.predictions} />
            </div>
          )}
        </main>
      </div>
    </>
  )
}
