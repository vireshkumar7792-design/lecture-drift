import { Component, type ReactNode } from 'react'
import { clearSession } from '../storage'

/** Shows a recovery screen instead of a blank page if a component crashes while rendering. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="grid h-full place-items-center p-6">
        <div className="max-w-md rounded-2xl border border-line bg-surface p-6 text-center">
          <h1 className="text-lg font-semibold">Something went wrong</h1>
          <p className="mt-2 text-sm text-muted">The page hit an unexpected error. Your lecture data on the server is safe.</p>
          <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-2 p-3 text-left text-xs text-danger">{this.state.error.message}</pre>
          <div className="mt-5 flex justify-center gap-2">
            <button type="button" onClick={() => location.reload()} className="rounded-lg border border-line px-4 py-2 text-sm hover:bg-surface-2">
              Reload
            </button>
            <button
              type="button"
              onClick={() => {
                clearSession()
                location.reload()
              }}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:brightness-110"
            >
              Start over
            </button>
          </div>
        </div>
      </div>
    )
  }
}
