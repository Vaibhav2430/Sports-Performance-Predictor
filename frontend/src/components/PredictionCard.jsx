const STATS = [
  { key: 'PTS', label: 'Points',   color: 'var(--pts)' },
  { key: 'AST', label: 'Assists',  color: 'var(--ast)' },
  { key: 'REB', label: 'Rebounds', color: 'var(--reb)' },
]

export default function PredictionCard({ predictions }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
      {STATS.map(({ key, label, color }) => {
        const p = predictions[key]
        return (
          <div key={key} className="card" style={{ textAlign: 'center', padding: '28px 20px' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1.2px', color: 'var(--muted)', marginBottom: 12 }}>
              {label}
            </div>
            <div style={{ fontSize: '3.2rem', fontWeight: 800, color, lineHeight: 1 }}>
              {p.prediction}
            </div>
            <div style={{ marginTop: 10, fontSize: '0.78rem', color: 'var(--muted)' }}>
              {p.floor} – {p.ceiling}
            </div>
            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-around', fontSize: '0.8rem' }}>
              <Stat label="L5 Avg" value={p.last5_avg} />
              <Stat label="Season" value={p.season_avg} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ color: 'var(--muted)', fontSize: '0.72rem', marginBottom: 2 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  )
}
