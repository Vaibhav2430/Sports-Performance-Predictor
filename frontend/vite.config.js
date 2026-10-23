import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { apiBaseUrl } from './src/apiConfig'

export default defineConfig(({ mode, command }) => {
  const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env }
  apiBaseUrl(env.VITE_API_BASE_URL, command === 'build')
  return {
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/predict':          'http://localhost:8000',
      '/search':           'http://localhost:8000',
      '/games/today':      'http://localhost:8000',
      '/team_ratings':     'http://localhost:8000',
      '/team_preview':     'http://localhost:8000',
      '/wnba':             'http://localhost:8000',
      '/accuracy':         'http://localhost:8000',
      '/chat':             'http://localhost:8000',
    },
  },
  }
})
