import { memo, useMemo, useState, type MouseEvent } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AlertCircle, Check, Copy, FileText, Loader2, Play, Square } from 'lucide-react'
import type { Source } from '../api'
import type { ChatMessage } from './StudyStep'

// Citation labels look like "L12 05:31" or "L3 1:02:03" (lecture position + timestamp).
const LABEL = String.raw`L\d+ (?:\d+:)?\d{2}:\d{2}`
const BRACKETED = new RegExp(String.raw`\[(${LABEL}(?:\s*[,;]\s*${LABEL})*)\](?!\()`, 'g')
const BARE = new RegExp(String.raw`(?<![\[\w])(${LABEL})(?![\]\w])`, 'g')

/** Turn citation labels into markdown links with a #cite= href that the renderer shows as chips. */
function linkCitations(text: string, known: Set<string>) {
  const toLink = (label: string) => `[${label}](#cite=${encodeURIComponent(label)})`
  return text
    .replace(BRACKETED, (_, inner: string) => inner.split(/\s*[,;]\s*/).map(toLink).join(' '))
    .replace(BARE, (match: string) => (known.has(match) ? toLink(match) : match))
}

interface Props {
  message: ChatMessage
  active: boolean
  onPlay: (source: Source) => void
  onShowSources: () => void
}

function Message({ message, active, onPlay, onShowSources }: Props) {
  if (message.role === 'user') {
    return (
      <div className="fade-up flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[0.95rem] whitespace-pre-wrap text-white shadow-sm">
          {message.content}
        </div>
      </div>
    )
  }
  return <AssistantMessage message={message} active={active} onPlay={onPlay} onShowSources={onShowSources} />
}

function AssistantMessage({ message, active, onPlay, onShowSources }: Props) {
  const [copied, setCopied] = useState(false)
  const sources = message.sources ?? []
  const byLabel = useMemo(() => new Map(sources.map((s) => [s.label, s])), [sources])
  const content = useMemo(() => linkCitations(message.content, new Set(byLabel.keys())), [message.content, byLabel])

  const components: Components = useMemo(
    () => ({
      a({ href, children }) {
        if (href?.startsWith('#cite=')) {
          const label = decodeURIComponent(href.slice(6))
          const source = byLabel.get(label)
          if (!source) return <span className="text-muted">[{label}]</span>
          return <CitationChip source={source} onPlay={onPlay} />
        }
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        )
      },
    }),
    [byLabel, onPlay],
  )

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked */
    }
  }

  const waiting = message.streaming && !message.content

  return (
    <div className="fade-up flex gap-3">
      <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-accent to-accent-2 text-xs font-bold text-white">
        AI
      </div>
      <div className="min-w-0 flex-1">
        {waiting && (
          <div className="flex items-center gap-2 py-1.5 text-sm text-muted">
            <Loader2 size={15} className="animate-spin text-accent" />
            {message.status ?? 'Thinking...'}
          </div>
        )}
        {message.content && (
          <div className={`md ${message.streaming ? 'streaming-caret' : ''}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {content}
            </ReactMarkdown>
          </div>
        )}
        {message.error && (
          <div className="mt-2 flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
            <AlertCircle size={16} className="mt-0.5 shrink-0" /> {message.error}
          </div>
        )}
        {message.stopped && (
          <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted">
            <Square size={11} /> Stopped
          </div>
        )}
        {!message.streaming && (message.content || sources.length > 0) && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {sources.length > 0 && (
              <button
                type="button"
                onClick={onShowSources}
                className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition
                  ${active ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-ink'}`}
              >
                <FileText size={13} /> {sources.length} transcript passages
              </button>
            )}
            {message.content && (
              <button
                type="button"
                onClick={copy}
                className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted transition hover:bg-surface-2 hover:text-ink"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function CitationChip({ source, onPlay }: { source: Source; onPlay: (source: Source) => void }) {
  const [time] = source.label.split(' ').slice(1)
  const click = (event: MouseEvent) => {
    // Plain click plays inside the app; Ctrl/Cmd-click still opens YouTube in a new tab.
    // Local files have no outside page to open, so they always play in the app.
    if ((event.ctrlKey || event.metaKey || event.shiftKey) && source.source !== 'local') return
    event.preventDefault()
    onPlay(source)
  }
  return (
    <a
      href={source.link}
      target="_blank"
      rel="noreferrer"
      onClick={click}
      title={`L${source.position}: ${source.title}\nClick to play at ${time}${source.source === 'local' ? '' : ' (Ctrl+click opens YouTube)'}`}
      className="cite mx-0.5 inline-flex translate-y-[-1px] items-center gap-1 rounded-md border border-accent/30 bg-accent-soft px-1.5 py-px align-middle font-mono text-[0.72rem] font-medium whitespace-nowrap text-accent no-underline transition hover:border-accent hover:bg-accent hover:text-white"
    >
      <Play size={9} fill="currentColor" />L{source.position} · {time}
    </a>
  )
}

export default memo(Message)
