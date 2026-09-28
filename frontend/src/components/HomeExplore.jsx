import { playerHeadshotUrl } from '../teamLogo'

const players = {
  NBA: [
    { name: 'LeBron James', initials: 'LJ', id: 2544 },
    { name: 'Stephen Curry', initials: 'SC', id: 201939 },
    { name: 'Nikola Jokic', initials: 'NJ', id: 203999 },
  ],
  WNBA: [
    { name: 'Caitlin Clark', initials: 'CC', espnId: 4433403 },
    { name: "A'ja Wilson", initials: 'AW', espnId: 3149391 },
    { name: 'Breanna Stewart', initials: 'BS', espnId: 2998928 },
  ],
}

export default function HomeExplore({ league, onSelectPlayer }) {
  return (
    <div className="home-explore fade-in">
      <section aria-labelledby="explore-title">
        <div className="home-section-heading">
          <h2 id="explore-title">Players to explore</h2>
        </div>
        <div className="explore-grid">
          {players[league].map(player => (
            <button className="explore-player" key={player.name} onClick={() => onSelectPlayer(player.name)}>
              <span className="explore-avatar" aria-hidden="true">
                <span>{player.initials}</span>
                {<img src={player.espnId ? `https://a.espncdn.com/i/headshots/wnba/players/full/${player.espnId}.png` : playerHeadshotUrl(player.id, false)} alt="" onError={event => { event.currentTarget.style.display = 'none' }} />}
              </span>
              <span className="explore-player-copy"><strong>{player.name}</strong><span>View projections</span></span>
              <span className="explore-arrow" aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      </section>

      <p className="home-footnote">Projections are estimates. Explore the data behind every number.</p>
    </div>
  )
}
