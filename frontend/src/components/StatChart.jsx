import {
  ResponsiveContainer, ComposedChart, Line, ReferenceLine,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'

const SERIES = [
  { key: 'PTS', label: 'Points',   color: '#4fc3f7' },
  { key: 'AST', label: 'Assists',  color: '#69db7c' },
  { key: 'REB', label: 'Rebounds', color: '#ffa94d' },
]

export default function StatChart({ gameLog, predictions }) {
  // chart expects oldest→newest; gameLog comes newest→oldest
  const data = [...gameLog].reverse().map(g => ({
    date: g.GAME_DATE.slice(5),   // "MM-DD"
    PTS:  g.PTS,
    AST:  g.AST,
    REB:  g.REB,
  }))

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    return (
      <div style={{ background: '#13162a', border: '1px solid #252840', borderRadius: 8, padding: '10px 14px', fontSize: '0.82rem' }}>
        <div style={{ color: '#7a7d99', marginBottom: 6 }}>{label}</div>
        {payload.map(p => (
          <div key={p.dataKey} style={{ color: p.color, marginBottom: 2 }}>
            {p.name}: <strong>{p.value}</strong>
          </div>
        ))}
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2240" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: '#7a7d99', fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: '#7a7d99', fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          domain={[0, 'auto']}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{ fontSize: '0.82rem', paddingTop: 12 }}
          formatter={(v, entry) => <span style={{ color: entry.color }}>{v}</span>}
        />
        {SERIES.map(({ key, label, color }) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            name={label}
            stroke={color}
            strokeWidth={2}
            dot={{ r: 3, fill: color, strokeWidth: 0 }}
            activeDot={{ r: 5 }}
          />
        ))}
        {/* Prediction reference lines */}
        {SERIES.map(({ key, color }) => (
          <ReferenceLine
            key={`ref-${key}`}
            y={predictions[key].prediction}
            stroke={color}
            strokeDasharray="5 4"
            strokeOpacity={0.5}
            strokeWidth={1.5}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
