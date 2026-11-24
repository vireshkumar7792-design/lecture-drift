import { useEffect, useMemo, useState } from 'react'
import { getStatus, type Lecture, type Playlist, type Status, type StudyMode, type TranscriptResult } from './api'
import Header, { type Step } from './components/Header'
import PlaylistStep from './components/PlaylistStep'
import SelectStep from './components/SelectStep'
import PrepareStep from './components/PrepareStep'
import StudyStep, { type ChatMessage } from './components/StudyStep'
import { clearSession, loadSession, saveSession } from './storage'

export interface Session {
  step: Step
  sources: Playlist[] // YouTube playlists and local folders, in the order they were added
  order: string[] // the student's teaching order of every lecture id from all sources
  selected: string[] // lecture ids chosen for comparison
  mode: StudyMode
  concept: string
  prepared: TranscriptResult | null
  messages: ChatMessage[]
}

const EMPTY: Session = {
  step: 'playlist',
  sources: [],
  order: [],
  selected: [],
  mode: 'comparison',
  concept: '',
  prepared: null,
  messages: [],
}

function restore(): Session {
  const saved = { ...EMPTY, ...loadSession<Session & { playlist?: Playlist | null }>() }
  // Sessions saved before multiple sources existed had a single `playlist`.
  if (saved.playlist && !saved.sources.length) {
    saved.sources = [saved.playlist]
    saved.order = saved.playlist.lectures.map((l) => l.video_id)
  }
  delete saved.playlist
  // Never restore a half-finished job or a half-streamed answer.
  if (saved.step === 'prepare') saved.step = saved.sources.length ? 'select' : 'playlist'
  if (saved.step === 'study' && !saved.prepared?.transcripts.length) saved.step = 'select'
  saved.messages = (saved.messages ?? []).filter((m) => !m.streaming)
  return saved
}

/** Every lecture from every source, in the student's order (new lectures go at the end). */
export function orderedPool(session: Pick<Session, 'sources' | 'order'>): Lecture[] {
  const byId = new Map<string, Lecture>()
  for (const source of session.sources)
    for (const lecture of source.lectures)
      if (!byId.has(lecture.video_id)) byId.set(lecture.video_id, { ...lecture, source: lecture.source ?? source.source ?? 'youtube' })
  const ids = [...session.order.filter((id) => byId.has(id)), ...[...byId.keys()].filter((id) => !session.order.includes(id))]
  return ids.map((id) => byId.get(id)!)
}

export default function App() {
  const [session, setSession] = useState<Session>(restore)
  const [status, setStatus] = useState<Status | null>(null)
  const update = (changes: Partial<Session>) => setSession((s) => ({ ...s, ...changes }))

  // Save shortly after changes settle, so a streaming answer does not write storage every frame.
  useEffect(() => {
    const timer = setTimeout(() => saveSession(session), 400)
    return () => clearTimeout(timer)
  }, [session])
  useEffect(() => {
    getStatus().then(setStatus).catch(() => setStatus(null))
  }, [])

  const pool = useMemo(() => orderedPool(session), [session.sources, session.order])
  // The teaching order decides each lecture's number (L1, L2, ...) for labels and comparison.
  const chosenLectures = useMemo(
    () =>
      (session.mode === 'reference' ? pool : pool.filter((l) => session.selected.includes(l.video_id))).map(
        (lecture, index) => ({ ...lecture, position: index + 1 }),
      ),
    [pool, session.mode, session.selected],
  )
  const kinds = useMemo(() => Object.fromEntries(pool.map((l) => [l.video_id, l.kind ?? 'video'])), [pool])

  const addSource = (source: Playlist) =>
    setSession((s) => {
      const sources = [...s.sources.filter((existing) => existing.url !== source.url), source]
      return { ...s, sources, step: 'select', prepared: null, messages: [], order: orderedPool({ sources, order: s.order }).map((l) => l.video_id) }
    })

  const removeSource = (url: string) =>
    setSession((s) => {
      const sources = s.sources.filter((source) => source.url !== url)
      const remaining = new Set(sources.flatMap((source) => source.lectures.map((l) => l.video_id)))
      return {
        ...s,
        sources,
        selected: s.selected.filter((id) => remaining.has(id)),
        step: sources.length ? 'select' : 'playlist',
        prepared: null,
        messages: [],
      }
    })

  const startOver = () => {
    clearSession()
    setSession(EMPTY)
  }

  return (
    <div className="flex h-full flex-col">
      <Header
        step={session.step}
        status={status}
        canGoTo={{
          playlist: true,
          select: session.sources.length > 0,
          prepare: false,
          study: !!session.prepared?.transcripts.length,
        }}
        onStep={(step) => update({ step })}
        onReset={startOver}
      />
      <main className="min-h-0 flex-1">
        {session.step === 'playlist' && (
          <PlaylistStep sources={session.sources} onLoaded={addSource} onContinue={() => update({ step: 'select' })} />
        )}
        {session.step === 'select' && session.sources.length > 0 && (
          <SelectStep
            sources={session.sources}
            pool={pool}
            chosen={chosenLectures}
            selected={session.selected}
            mode={session.mode}
            concept={session.concept}
            onChange={(changes) => update({ ...changes, prepared: null, messages: [] })}
            onAddSource={() => update({ step: 'playlist' })}
            onRemoveSource={removeSource}
            onStart={() => update({ step: 'prepare' })}
          />
        )}
        {session.step === 'prepare' && (
          <PrepareStep
            lectures={chosenLectures}
            mode={session.mode}
            onBack={() => update({ step: 'select' })}
            onReady={(prepared) => update({ prepared, step: 'study', messages: [] })}
          />
        )}
        {session.step === 'study' && session.prepared && (
          <StudyStep
            prepared={session.prepared}
            kinds={kinds}
            mode={session.mode}
            concept={session.concept}
            llmReady={status?.llm_ready ?? true}
            messages={session.messages}
            setMessages={(updater) => setSession((s) => ({ ...s, messages: updater(s.messages) }))}
            onEditSelection={() => update({ step: 'select' })}
          />
        )}
      </main>
    </div>
  )
}
