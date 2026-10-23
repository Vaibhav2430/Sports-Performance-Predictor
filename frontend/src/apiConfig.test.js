import test from 'node:test'
import assert from 'node:assert/strict'
import { apiBaseUrl } from './apiConfig.js'

test('local proxy and same-origin gateways remain usable', () => {
  assert.equal(apiBaseUrl(), '')
  assert.equal(apiBaseUrl('/api/'), '/api')
})
test('production accepts a public HTTPS backend and trims its trailing slash', () => {
  assert.equal(apiBaseUrl(' https://api.example.com/ ', true), 'https://api.example.com')
})
test('production rejects insecure or private API URL settings', () => {
  for (const value of ['http://api.example.com', 'https://localhost', 'https://user:password@example.com', 'https://example.com?key=secret', 'javascript:alert(1)']) {
    assert.throws(() => apiBaseUrl(value, true))
  }
})
