export default function GameLogTable({ gameLog, predictions }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th style={{ textAlign: 'left'   }}>Date</th>
            <th style={{ textAlign: 'left'   }}>Matchup</th>
            <th style={{ textAlign: 'center' }}>W/L</th>
            <th style={{ textAlign: 'center' }}>Min</th>
            <th style={{ textAlign: 'center' }}>PTS</th>
            <th style={{ textAlign: 'center' }}>AST</th>
            <th style={{ textAlign: 'center' }}>REB</th>
          </tr>
        </thead>
        <tbody>
          {gameLog.map((row, i) => (
            <tr key={i}>
              <td className="td-muted">{row.GAME_DATE}</td>
              <td style={{ fontWeight: 500, fontSize: '0.85rem' }}>{row.MATCHUP}</td>
              <td className={row.WL === 'W' ? 'td-win' : 'td-loss'} style={{ textAlign: 'center' }}>
                {row.WL}
              </td>
              <td className="td-muted" style={{ textAlign: 'center' }}>{row.MIN}</td>
              <td className="td-pts" style={{ textAlign: 'center' }}>
                {row.PTS}
                {row.PTS > predictions.PTS.prediction && <sup style={{ color: '#22c55e', fontSize: '0.6rem', marginLeft: 2 }}>↑</sup>}
              </td>
              <td className="td-ast" style={{ textAlign: 'center' }}>
                {row.AST}
                {row.AST > predictions.AST.prediction && <sup style={{ color: '#22c55e', fontSize: '0.6rem', marginLeft: 2 }}>↑</sup>}
              </td>
              <td className="td-reb" style={{ textAlign: 'center' }}>
                {row.REB}
                {row.REB > predictions.REB.prediction && <sup style={{ color: '#22c55e', fontSize: '0.6rem', marginLeft: 2 }}>↑</sup>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
