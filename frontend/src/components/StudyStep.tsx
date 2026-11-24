import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import {
  ArrowUp,
  ExternalLink,
  FileText,
  Lightbulb,
  ListVideo,
  MessageSquare,
  PlayCircle,
  Settings2,
  Sparkles,
  Square,
  Trash2,
  Tv,
} from 'lucide-react'
import {
  formatDuration,
  formatTime,
  isLocal,
  streamEvents,
  type Source,
  type StudyMode,
  type TranscriptResult,
} from '../api'
import Message, { CitationChip } from './Message'
import LectureThumb from './LectureThumb'
import LocalPlayer from './LocalPlayer'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  status?: string
  streaming?: boolean
  stopped?: boolean
  error?: string
}

interface Props {
  prepared: TranscriptResult
  kinds: Record<string, 'audio' | 'video'> // media type of local lectures, by id
  mode: StudyMode
  concept: string
  llmReady: boolean
  messages: ChatMessage[]
  setMessages: (updater: (messages: ChatMessage[]) => ChatMessage[]) => void
  onEditSelection: () => void
}

interface Playing {
  videoId: string
  local: boolean
  start: number
  title: string
  position: number
  nonce: number
}

type Panel = 'chat' | 'video' | 'lectures'

const newId = () => Math.random().toString(36).slice(2, 10)

export default function StudyStep({ prepared, kinds, mode, concept, llmReady, messages, setMessages, onEditSelection }: Props) {
  const lectures = prepared.transcripts
  const [input, setInput] = useState('')
  const [playing, setPlaying] = useState<Playing | null>(null)
  const [activeAnswerId, setActiveAnswerId] = useState<string | null>(null)
  const [panel, setPanel] = useState<Panel>('chat')
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const busy = messages.some((m) => m.streaming)
  const answers = messages.filter((m) => m.role === 'assistant')
  const activeAnswer = answers.find((m) => m.id === activeAnswerId) ?? answers[answers.length - 1]
  const activeSources = activeAnswer?.sources ?? []

  const patch = useCallback(
    (id: string, change: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) =>
      setMessages((all) => all.map((m) => (m.id === id ? { ...m, ...(typeof change === 'function' ? change(m) : change) } : m))),
    [setMessages],
  )

  // Follow the stream while the student is at the bottom; stop following if they scroll up to read.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [messages])
  const onScroll = () => {
    const el = scrollRef.current
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  useEffect(() => () => abortRef.current?.abort(), [])

  const play = useCallback((source: { video_id: string; start: number; title: string; position: number; source?: Source['source'] }) => {
    setPlaying({
      videoId: source.video_id,
      local: isLocal(source),
      start: Math.floor(source.start),
      title: source.title,
      position: source.position,
      nonce: Date.now(),
    })
    setPanel((p) => (p === 'chat' ? 'video' : p))
  }, [])

  const ask = async (raw: string) => {
    const question = raw.trim()
    if (!question || busy) return
    setInput('')
    stickToBottom.current = true
    const answerId = newId()
    const history = messages
      .filter((m) => m.content && !m.error && !m.streaming)
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }))
    setMessages((all) => [
      ...all,
      { id: newId(), role: 'user', content: question },
      { id: answerId, role: 'assistant', content: '', streaming: true, status: 'Starting...' },
    ])
    setActiveAnswerId(answerId)

    const controller = new AbortController()
    abortRef.current = controller
    // Tokens arrive faster than the screen refreshes; batch them into one update per frame.
    let pending = ''
    let frame = 0
    const flush = () => {
      frame = 0
      if (!pending) return
      const text = pending
      pending = ''
      patch(answerId, (m) => ({ content: m.content + text }))
    }
    let failure = ''

    try {
      await streamEvents(
        '/api/chat',
        { video_ids: lectures.map((l) => l.video_id), question, mode, concept, history },
        (event) => {
          if (event.type === 'status') patch(answerId, { status: event.message })
          else if (event.type === 'sources') patch(answerId, { sources: event.sources })
          else if (event.type === 'token') {
            pending += event.text
            if (!frame) frame = requestAnimationFrame(flush)
          } else if (event.type === 'error') failure = event.message
        },
        controller.signal,
      )
    } catch (e) {
      if (controller.signal.aborted) patch(answerId, { stopped: true })
      else failure = e instanceof Error ? e.message : String(e)
    } finally {
      cancelAnimationFrame(frame)
      flush()
      patch(answerId, { streaming: false, status: undefined, ...(failure ? { error: failure } : {}) })
      abortRef.current = null
      inputRef.current?.focus()
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      ask(input)
    }
  }

  const suggestions = useMemo(() => {
    const list: string[] = []
    if (concept.trim()) list.push(concept.trim())
    list.push(mode === 'comparison' ? 'How do these lectures build on each other?' : 'Give me an overview of the main ideas in these lectures')
    list.push(`What are the key takeaways from L${lectures[0]?.position}?`)
    list.push('Quiz me with 5 questions on these lectures, with answers')
    return list
  }, [concept, mode, lectures])

  const totalMinutes = lectures.reduce((sum, l) => sum + l.duration, 0)

  const tabs: { id: Panel; label: string; icon: typeof Tv }[] = [
    { id: 'lectures', label: 'Lectures', icon: ListVideo },
    { id: 'chat', label: 'Chat', icon: MessageSquare },
    { id: 'video', label: 'Video & sources', icon: Tv },
  ]

  return (
    <div className="flex h-full flex-col">
      {/* Panel switcher on small screens */}
      <div className="flex border-b border-line bg-surface lg:hidden">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setPanel(id)}
            className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-2.5 text-xs font-medium
              ${panel === id ? 'border-accent text-accent' : 'border-transparent text-muted'}`}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      <div className="mx-auto grid min-h-0 w-full max-w-[1600px] flex-1 lg:grid-cols-[260px_minmax(0,1fr)_400px] xl:grid-cols-[280px_minmax(0,1fr)_460px]">
        {/* Lectures */}
        <aside className={`${panel === 'lectures' ? 'flex' : 'hidden'} min-h-0 flex-col border-line lg:flex lg:border-r`}>
          <div className="flex items-center justify-between px-4 pt-4 pb-2">
            <div>
              <div className="text-sm font-semibold">{mode === 'comparison' ? 'Comparing' : 'Reference'}</div>
              <div className="text-xs text-muted">
                {lectures.length} lectures · {formatDuration(totalMinutes)}
              </div>
            </div>
            <button type="button" onClick={onEditSelection} title="Change lectures" className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink">
              <Settings2 size={16} />
            </button>
          </div>
          {concept.trim() && (
            <div className="mx-3 mb-2 rounded-xl bg-accent-soft p-2.5 text-xs">
              <div className="mb-0.5 flex items-center gap-1 font-semibold text-accent">
                <Lightbulb size={12} /> Your focus
              </div>
              <div className="line-clamp-3 text-ink/80">{concept}</div>
            </div>
          )}
          <div className="scroll-thin min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-4">
            {lectures.map((lecture) => (
              <button
                key={lecture.video_id}
                type="button"
                onClick={() => play({ ...lecture, start: 0 })}
                className={`group flex w-full items-center gap-2.5 rounded-lg p-1.5 text-left transition hover:bg-surface-2
                  ${playing?.videoId === lecture.video_id ? 'bg-surface-2' : ''}`}
              >
                <div className="relative aspect-video w-20 shrink-0 overflow-hidden rounded-md bg-surface-2">
                  <LectureThumb lecture={{ ...lecture, kind: kinds[lecture.video_id] }} className="size-full" />
                  <PlayCircle size={20} className="absolute inset-0 m-auto text-white opacity-0 drop-shadow transition group-hover:opacity-100" />
                </div>
                <div className="min-w-0">
                  <div className="line-clamp-2 text-xs leading-snug font-medium">{lecture.title}</div>
                  <div className="mt-0.5 text-[11px] text-muted">
                    L{lecture.position} · {formatDuration(lecture.duration)} ·{' '}
                    <span title={lecture.text_source === 'whisper' ? 'Transcribed with Whisper on this computer' : 'YouTube captions'}>
                      {lecture.text_source === 'whisper' ? 'Whisper' : 'Captions'}
                    </span>
                  </div>
                </div>
              </button>
            ))}
            {prepared.deferred.length > 0 && (
              <p className="px-2 pt-2 text-[11px] text-muted">{prepared.deferred.length} lectures without captions are not included yet.</p>
            )}
          </div>
        </aside>

        {/* Chat */}
        <section className={`${panel === 'chat' ? 'flex' : 'hidden'} min-h-0 flex-col lg:flex`}>
          <div ref={scrollRef} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
              {messages.length === 0 ? (
                <EmptyChat suggestions={suggestions} onPick={ask} llmReady={llmReady} />
              ) : (
                messages.map((m) => (
                  <Message
                    key={m.id}
                    message={m}
                    active={m.id === activeAnswer?.id}
                    onPlay={play}
                    onShowSources={() => {
                      setActiveAnswerId(m.id)
                      setPanel('video')
                    }}
                  />
                ))
              )}
            </div>
          </div>

          <div className="border-t border-line bg-bg/80 px-4 pt-3 pb-4 backdrop-blur">
            <div className="mx-auto max-w-3xl">
              <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-sm transition focus-within:border-accent">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  rows={1}
                  placeholder={llmReady ? 'Ask anything about these lectures...' : 'Add OPENROUTER_API_KEY to .env to start chatting'}
                  aria-label="Your question"
                  className="field-sizing-content max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted/70"
                />
                {busy ? (
                  <button
                    type="button"
                    onClick={() => abortRef.current?.abort()}
                    title="Stop generating"
                    className="grid size-10 shrink-0 place-items-center rounded-xl bg-ink text-bg transition hover:opacity-80"
                  >
                    <Square size={14} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => ask(input)}
                    disabled={!input.trim()}
                    title="Send (Enter)"
                    className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-white transition hover:brightness-110 disabled:opacity-40"
                  >
                    <ArrowUp size={18} />
                  </button>
                )}
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                <span>Answers use only your lecture transcripts. Captions can contain recognition errors.</span>
                {messages.length > 0 && !busy && (
                  <button type="button" onClick={() => setMessages(() => [])} className="flex shrink-0 items-center gap-1 hover:text-ink">
                    <Trash2 size={11} /> Clear chat
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* Video + sources */}
        <aside className={`${panel === 'video' ? 'flex' : 'hidden'} min-h-0 flex-col border-line lg:flex lg:border-l`}>
          <div className="p-4 pb-3">
            {playing ? (
              <div>
                {playing.local ? (
                  <LocalPlayer
                    lectureId={playing.videoId}
                    kind={kinds[playing.videoId] ?? 'video'}
                    start={playing.start}
                    nonce={playing.nonce}
                    title={playing.title}
                  />
                ) : (
                  <div className="aspect-video overflow-hidden rounded-xl bg-black shadow-md">
                    <iframe
                      key={`${playing.videoId}-${playing.start}-${playing.nonce}`}
                      src={`https://www.youtube-nocookie.com/embed/${playing.videoId}?start=${playing.start}&autoplay=1&rel=0&modestbranding=1`}
                      title={playing.title}
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                      allowFullScreen
                      className="size-full"
                    />
                  </div>
                )}
                <div className="mt-2 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{playing.title}</div>
                    <div className="text-xs text-muted">
                      L{playing.position} · from {formatTime(playing.start)}
                      {playing.local && ' · file on this computer'}
                    </div>
                  </div>
                  {!playing.local && (
                  <a
                    href={`https://www.youtube.com/watch?v=${playing.videoId}&t=${playing.start}s`}
                    target="_blank"
                    rel="noreferrer"
                    title="Open on YouTube"
                    className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink"
                  >
                    <ExternalLink size={15} />
                  </a>
                  )}
                </div>
              </div>
            ) : (
              <div className="grid aspect-video place-items-center rounded-xl border border-dashed border-line bg-surface text-center">
                <div className="px-6">
                  <PlayCircle size={28} className="mx-auto text-muted" />
                  <p className="mt-2 text-sm font-medium">Watch exactly what you need</p>
                  <p className="mt-1 text-xs text-muted">Click any citation in an answer to play that moment here.</p>
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-1.5 border-t border-line px-4 pt-3 pb-2 text-sm font-semibold">
            <FileText size={15} className="text-muted" /> Sources
            {activeSources.length > 0 && <span className="text-xs font-normal text-muted">({activeSources.length})</span>}
          </div>
          <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-y-auto px-4 pb-4">
            {activeSources.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted">The transcript passages behind each answer appear here.</p>
            ) : (
              activeSources.map((source) => <SourceCard key={source.label} source={source} onPlay={play} />)
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}

function SourceCard({ source, onPlay }: { source: Source; onPlay: (s: Source) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <CitationChip source={source} onPlay={onPlay} />
        <span className="font-mono text-[11px] text-muted">
          {formatTime(source.start)}–{formatTime(source.end)}
        </span>
      </div>
      <div className="mt-1.5 line-clamp-1 text-xs font-medium">{source.title}</div>
      <button type="button" onClick={() => setOpen((o) => !o)} className="mt-1 w-full text-left">
        <p className={`text-xs leading-relaxed text-muted ${open ? '' : 'line-clamp-3'}`}>{source.text}</p>
      </button>
    </div>
  )
}

function EmptyChat({ suggestions, onPick, llmReady }: { suggestions: string[]; onPick: (q: string) => void; llmReady: boolean }) {
  return (
    <div className="flex flex-col items-center pt-8 text-center sm:pt-16">
      <div className="grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-lg">
        <Sparkles size={22} />
      </div>
      <h2 className="mt-4 text-xl font-semibold tracking-tight">What do you want to learn?</h2>
      <p className="mt-1 max-w-md text-sm text-muted">
        Ask a question and the answer streams in, citing the exact lecture moments it comes from.
      </p>
      {!llmReady && (
        <p className="mt-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          No API key found. Add OPENROUTER_API_KEY to the .env file and restart the server.
        </p>
      )}
      <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-xl border border-line bg-surface p-3 text-left text-sm transition hover:border-accent hover:bg-accent-soft"
          >
            <span className="line-clamp-2">{s}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
