import axios from 'axios'
import { apiBaseUrl } from './apiConfig'

// Public backend address only. No database/admin/API secrets belong here.
export default axios.create({
  baseURL: apiBaseUrl(import.meta.env.VITE_API_BASE_URL, import.meta.env.PROD),
  timeout: 120000,
  withCredentials: false,
})
