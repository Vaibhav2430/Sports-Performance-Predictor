export function apiBaseUrl(value = '', production = false) {
  const base = value.trim().replace(/\/+$/, '')
  // An empty base uses the current website origin (development proxy or a
  // configured production reverse proxy). /api supports a same-origin gateway.
  if (!base) return ''
  if (base.startsWith('/') && !base.startsWith('//')) {
    if (/[?#\\]/.test(base)) throw new Error('API path must not contain a query, fragment, or backslash.')
    return base
  }
  let url
  try { url = new URL(base) } catch { throw new Error('VITE_API_BASE_URL must be an HTTPS backend URL or a relative API path.') }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('API URL must use HTTP(S) without credentials, query parameters, or fragments.')
  }
  if (production && (url.protocol !== 'https:' || ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) {
    throw new Error('The production API URL must use HTTPS and a public backend hostname.')
  }
  return base
}
