// Typed client for the FastAPI backend (server.py).

export type LectureSource = 'youtube' | 'local'

export interface Lecture {
  position: number
  video_id: string // YouTube id, or "local_<hash>" for a file on this computer
  title: string
  source?: LectureSource // missing means 'youtube' (sessions saved before local files existed)
  url: string
  file_name?: string // local files only
  kind?: 'audio' | 'video' // local files only
  size?: number // local files only, bytes
}

/** A loaded lecture source: a YouTube playlist or a folder on this computer. */
export interface Playlist {
  title: string
  url: string // playlist link, or the folder path
  source?: LectureSource
  lectures: Lecture[]
  skipped: number
}

export interface TranscriptInfo extends Lecture {
  text_source: string // 'youtube_captions' or 'whisper'
  language: string
  is_generated: boolean
  segments: number
  duration: number
}

export interface DeferredLecture extends Lecture {
  reason: string
}

export interface TranscriptResult {
  transcripts: TranscriptInfo[]
  deferred: DeferredLecture[]
  errors: { title: string; error: string; video_id?: string }[]
}

/** Progress from a long job. Whisper jobs add which lecture, its stage and a percentage. */
export interface ProgressUpdate {
  message: string
  lecture?: string
  stage?: 'downloading' | 'transcribing' | 'done' | 'failed'
  percent?: number
}

export interface Source {
  label: string
  link: string
  source: LectureSource
  video_id: string
  position: number
  title: string
  start: number
  end: number
  text: string
}

export interface Status {
  llm_ready: boolean
  llm_model: string | null
  embedding_model: string
}

export type StudyMode = 'comparison' | 'reference'

export type StreamEvent =
  | ({ type: 'progress' } & ProgressUpdate)
  | { type: 'status'; message: string }
  | { type: 'result'; data: unknown }
  | { type: 'sources'; sources: Source[]; query: string }
  | { type: 'token'; text: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

async function errorMessage(response: Response) {
  // The page can be newer than a server that was started before an update (it serves the latest build).
  if (response.status === 405 || (response.status === 404 && new URL(response.url).pathname.startsWith('/api/')))
    return 'The server is running an older version of the app. Stop it (Ctrl+C in its terminal) and start it again with: python server.py'
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join('; ')
  } catch {
    /* not JSON */
  }
  return `Request failed (${response.status})`
}

export async function getStatus(): Promise<Status> {
  const response = await fetch('/api/status')
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

export async function loadPlaylist(url: string): Promise<Playlist> {
  const response = await fetch('/api/playlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

/** POST a JSON body and call onEvent for every line of the NDJSON stream the server sends back. */
export async function loadLocalFolder(path: string, recursive: boolean): Promise<Playlist> {
  const response = await fetch('/api/local-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, recursive }),
  })
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

export async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) throw new Error(await errorMessage(response))

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let newline
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim()
      buffer = buffer.slice(newline + 1)
      if (line) onEvent(JSON.parse(line) as StreamEvent)
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent)
}

/** Run a streamed background job (transcripts, index) and resolve with its final result. */
export async function runJob<T>(
  path: string,
  body: unknown,
  onProgress: (update: ProgressUpdate) => void,
  signal?: AbortSignal,
): Promise<T> {
  let result: T | undefined
  let failure: string | undefined
  await streamEvents(path, body, (event) => {
    if (event.type === 'progress') onProgress(event)
    else if (event.type === 'result') result = event.data as T
    else if (event.type === 'error') failure = event.message
  }, signal)
  if (failure) throw new Error(failure)
  if (result === undefined) throw new Error('The server closed the connection before finishing.')
  return result
}

export function formatTime(seconds: number) {
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${String(m).padStart(2, '0')}:${sec}`
}

export function formatDuration(seconds: number) {
  const minutes = Math.round(seconds / 60)
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes} min`
}

export const thumbnail = (videoId: string) => `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`

export const isLocal = (lecture: { source?: LectureSource; video_id: string }) =>
  lecture.source === 'local' || lecture.video_id.startsWith('local_')

export function formatSize(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`
}
