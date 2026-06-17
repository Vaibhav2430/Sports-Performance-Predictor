import { useState } from 'react'
import axios from 'axios'
import PlayerSearch from './components/PlayerSearch'
import PredictionCard from './components/PredictionCard'
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
      setError(err.response?.data?.detail ?? 'Something went wrong. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 36 }}>
        <h1>🏀 NBA Stat Predictor</h1>
        <p style={{ color: 'var(--muted)', marginTop: 6, fontSize: '0.95rem' }}>
          XGBoost predictions for points, assists &amp; rebounds — next game.
        </p>
      </div>

      <PlayerSearch onSearch={handleSearch} loading={loading} />

      {loading && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--muted)' }}>
          <Spinner />
          <p style={{ marginTop: 16, fontSize: '0.9rem' }}>
            Fetching game logs and training model…&nbsp;(~10s)
          </p>
        </div>
      )}

      {error && (
        <div style={{
          marginTop: 24,
          background: '#2a1a1a',
          border: '1px solid #5a2020',
          borderRadius: 10,
          padding: '14px 18px',
          color: '#ff8080',
          fontSize: '0.9rem',
        }}>
          {error}
        </div>
      )}

      {data && !loading && (
        <div style={{ marginTop: 32, display: 'flex', flexDirection: 'column', gap: 32 }}>
          <div>
            <h1 style={{ fontSize: '1.4rem' }}>{data.player}</h1>
            <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginTop: 4 }}>
              {data.season} season · {data.games_used} games · recent form weighted higher
            </p>
          </div>

          <section>
            <h2>Next Game Prediction</h2>
            <PredictionCard predictions={data.predictions} />
          </section>

          <section>
            <h2>Last 20 Games</h2>
            <div className="card">
              <StatChart gameLog={data.game_log} predictions={data.predictions} />
            </div>
          </section>

          <section>
            <h2>Game Log</h2>
            <GameLogTable gameLog={data.game_log} />
          </section>
        </div>
      )}
    </div>
  )
}

function Spinner() {
  return (
    <div style={{
      width: 40, height: 40, border: '3px solid var(--border)',
      borderTopColor: 'var(--accent)', borderRadius: '50%',
      animation: 'spin 0.8s linear infinite', margin: '0 auto',
    }} />
  )
}

// inject keyframes once
const style = document.createElement('style')
style.textContent = '@keyframes spin { to { transform: rotate(360deg) } }'
document.head.appendChild(style)
