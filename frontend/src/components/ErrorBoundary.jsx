import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: '40px', color: '#fca5a5',
          background: '#09090b', minHeight: '100vh',
          fontFamily: 'monospace', fontSize: '0.85rem',
        }}>
          <h2 style={{ color: '#f97316', marginBottom: 12 }}>React Error</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{this.state.error.message}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#71717a', marginTop: 12 }}>
            {this.state.error.stack}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}
