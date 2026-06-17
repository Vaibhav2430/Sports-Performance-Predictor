const COL = [
  { key: 'GAME_DATE', label: 'Date',    align: 'left'  },
  { key: 'MATCHUP',   label: 'Matchup', align: 'left'  },
  { key: 'WL',        label: 'W/L',     align: 'center'},
  { key: 'MIN',       label: 'Min',     align: 'center'},
  { key: 'PTS',       label: 'PTS',     align: 'center', color: 'var(--pts)' },
  { key: 'AST',       label: 'AST',     align: 'center', color: 'var(--ast)' },
  { key: 'REB',       label: 'REB',     align: 'center', color: 'var(--reb)' },
]

export default function GameLogTable({ gameLog }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {COL.map(c => (
              <th key={c.key} style={{
                padding: '12px 16px',
                textAlign: c.align,
                color: 'var(--muted)',
                fontWeight: 500,
                fontSize: '0.75rem',
                textTransform: 'uppercase',
                letterSpacing: '0.8px',
              }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {gameLog.map((row, i) => (
            <tr
              key={i}
              style={{
                borderBottom: i < gameLog.length - 1 ? '1px solid var(--border)' : 'none',
                background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
              }}
            >
              {COL.map(c => (
                <td key={c.key} style={{
                  padding: '11px 16px',
                  textAlign: c.align,
                  color: c.key === 'WL'
                    ? row.WL === 'W' ? '#69db7c' : '#ff6b6b'
                    : c.color ?? 'var(--text)',
                  fontWeight: ['PTS','AST','REB'].includes(c.key) ? 600 : 400,
                }}>
                  {row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
