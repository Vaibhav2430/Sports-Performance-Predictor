import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

export default function PlayerSearch({ onSearch, loading, league }) {
  const [query, setQuery]       = useState('')
  const [suggestions, setSugg]  = useState([])
  const [showSugg, setShowSugg] = useState(false)
  const debounce    = useRef(null)
  const didMount    = useRef(false)
  const submitted   = useRef(false)

  async function fetchSuggestions(q) {
    try {
      const endpoint = league === 'WNBA' ? '/wnba/search' : '/search'
      const res = await axios.get(endpoint, { params: { q } })
      setSugg(res.data)
      if (!submitted.current) setShowSugg(true)
    } catch {
      setSugg([])
    }
  }

  useEffect(() => {
    if (!didMount.current) return
    clearTimeout(debounce.current)
    debounce.current = setTimeout(() => fetchSuggestions(query), query ? 200 : 0)
  }, [query, league])

  function submit(name) {
    const t = (name ?? query).trim()
    if (!t) return
    setQuery(t)
    setShowSugg(false)
    setSugg([])
    submitted.current = true
    onSearch(t)
  }

  function handleFocus() {
    if (!didMount.current) {
      didMount.current = true
      return
    }
    if (submitted.current) return
    if (suggestions.length > 0) {
      setShowSugg(true)
    } else {
      fetchSuggestions(query)
    }
  }

  return (
    <div className="search-row">
      <div className="search-inner">
        <svg className="search-icon" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input
          className="search-input"
          aria-label={`Search ${league} player`}
          value={query}
          onChange={e => { submitted.current = false; setQuery(e.target.value) }}
          onKeyDown={e => e.key === 'Enter' && submit()}
          onFocus={handleFocus}
          onBlur={() => setTimeout(() => setShowSugg(false), 150)}
          placeholder={league === 'WNBA' ? 'Search player — e.g. Caitlin Clark, A\'ja Wilson…' : 'Search player — e.g. LeBron James, Steph Curry…'}
          disabled={loading}
        />
        {showSugg && suggestions.length > 0 && (
          <ul className="dropdown">
            {suggestions.map(name => (
              <li key={name} onMouseDown={() => submit(name)}>{name}</li>
            ))}
          </ul>
        )}
      </div>
      <button className="search-btn" onClick={() => submit()} disabled={loading || !query.trim()}>
        {loading ? '…' : 'Predict'}
      </button>
    </div>
  )
}
