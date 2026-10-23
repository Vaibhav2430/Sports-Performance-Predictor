import { useState } from 'react'

export default function PlayerAvatar({ name, headshotUrl, fallbackUrl }) {
  const [stage, setStage] = useState(headshotUrl ? 0 : 1)
  const source = stage === 0 ? headshotUrl : stage === 1 ? fallbackUrl : null
  const initials = name.split(/\s+/).map(part => part[0]).filter(Boolean).slice(0, 2).join('')

  return (
    <div className="player-avi player-headshot">
      {source ? <img className={stage === 0 ? 'player-portrait' : 'player-avatar-fallback'}
        src={source} alt={stage === 0 ? `${name} headshot` : `${name} team logo`}
        onError={() => setStage(current => current + 1)} />
        : <span aria-label={name}>{initials}</span>}
    </div>
  )
}
