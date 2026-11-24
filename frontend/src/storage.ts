// Remember the current study session in this browser so a page refresh does not lose it.
// Storage can be unavailable (private mode, blocked site data), so every access is guarded.

const KEY = 'lecture-drift-session-v1'

export function loadSession<T>(): Partial<T> {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Partial<T>) : {}
  } catch {
    return {}
  }
}

export function saveSession<T>(session: T) {
  try {
    localStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    /* quota exceeded or storage blocked: the app still works, it just will not survive a refresh */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}
