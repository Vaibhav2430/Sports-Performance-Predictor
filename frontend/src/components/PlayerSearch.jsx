import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

export default function PlayerSearch({ onSearch, loading }) {
  const [query, setQuery]           = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [showSugg, setShowSugg]     = useState(false)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (query.length < 2) { setSuggestions([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await axios.get('/search', { params: { q: query } })
        setSuggestions(res.data)
        setShowSugg(true)
      } catch {
        setSuggestions([])
      }
    }, 250)
  }, [query])

  function submit(name) {
    const target = name ?? query
    if (!target.trim()) return
    setQuery(target)
    setShowSugg(false)
    onSearch(target.trim())
  }

  return (
    <div style={{ position: 'relative', display: 'flex', gap: 10, maxWidth: 560 }}>
      <div style={{ position: 'relative', flex: 1 }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          onFocus={() => suggestions.length && setShowSugg(true)}
          onBlur={() => setTimeout(() => setShowSugg(false), 150)}
          placeholder="e.g. LeBron James, Stephen Curry…"
          disabled={loading}
          style={inputStyle}
        />
        {showSugg && suggestions.length > 0 && (
          <ul style={dropdownStyle}>
            {suggestions.map(name => (
              <li
                key={name}
                onMouseDown={() => submit(name)}
                style={suggItemStyle}
                onMouseEnter={e => e.target.style.background = '#252840'}
                onMouseLeave={e => e.target.style.background = 'transparent'}
              >
                {name}
              </li>
            ))}
          </ul>
        )}
      </div>
      <button onClick={() => submit()} disabled={loading || !query.trim()} style={btnStyle}>
        {loading ? '…' : 'Predict'}
      </button>
    </div>
  )
}

const inputStyle = {
  width: '100%',
  padding: '12px 16px',
  background: '#13162a',
  border: '1px solid #252840',
  borderRadius: 10,
  color: '#e8eaf0',
  fontSize: '0.95rem',
  outline: 'none',
}

const btnStyle = {
  padding: '12px 22px',
  background: '#6c63ff',
  color: '#fff',
  border: 'none',
  borderRadius: 10,
  fontSize: '0.95rem',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  opacity: 1,
}

const dropdownStyle = {
  position: 'absolute',
  top: '100%',
  left: 0,
  right: 0,
  background: '#13162a',
  border: '1px solid #252840',
  borderRadius: 10,
  marginTop: 4,
  listStyle: 'none',
  zIndex: 100,
  overflow: 'hidden',
}

const suggItemStyle = {
  padding: '10px 16px',
  cursor: 'pointer',
  fontSize: '0.9rem',
  transition: 'background 0.1s',
}
