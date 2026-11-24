import { useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  BookOpenCheck,
  Folder,
  FolderOpen,
  Link2,
  Loader2,
  MessagesSquare,
  PlayCircle,
  Sparkles,
  Youtube,
} from 'lucide-react'
import { loadLocalFolder, loadPlaylist, type Playlist } from '../api'

interface Props {
  sources: Playlist[] // already loaded; when not empty this screen adds another source
  onLoaded: (source: Playlist) => void
  onContinue: () => void
}

type Kind = 'youtube' | 'folder'

const FEATURES = [
  { icon: BookOpenCheck, title: 'Grounded in the lectures', text: 'Answers come only from the transcripts you pick.' },
  { icon: PlayCircle, title: 'Jump to the moment', text: 'Every claim cites a timestamp that plays the lecture there.' },
  { icon: MessagesSquare, title: 'Compare explanations', text: 'See how a concept is introduced and refined across lectures.' },
]

export default function PlaylistStep({ sources, onLoaded, onContinue }: Props) {
  const adding = sources.length > 0
  const [kind, setKind] = useState<Kind>('youtube')
  const [url, setUrl] = useState('')
  const [folder, setFolder] = useState('')
  const [recursive, setRecursive] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const value = kind === 'youtube' ? url : folder

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!value.trim()) return
    setLoading(true)
    setError('')
    try {
      onLoaded(kind === 'youtube' ? await loadPlaylist(url) : await loadLocalFolder(folder, recursive))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="hero-glow h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col items-center px-4 pt-14 pb-12 text-center sm:pt-20">
        {adding ? (
          <>
            <button type="button" onClick={onContinue} className="mb-6 inline-flex items-center gap-1.5 self-start text-sm text-muted hover:text-ink">
              <ArrowLeft size={15} /> Back to lectures
            </button>
            <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Add another source</h1>
            <p className="mt-3 max-w-xl text-muted">
              Mix a YouTube playlist with recordings on your computer, then put all the lectures in your own order.
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {sources.map((source) => (
                <span key={source.url} className="inline-flex max-w-xs items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs">
                  {source.source === 'local' ? <Folder size={12} className="text-accent" /> : <Youtube size={12} className="text-danger" />}
                  <span className="truncate">{source.title}</span>
                  <span className="text-muted">· {source.lectures.length}</span>
                </span>
              ))}
            </div>
          </>
        ) : (
          <>
            <span className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted">
              <Sparkles size={13} className="text-accent" /> AI study companion for lecture courses
            </span>
            <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
              Learn a whole course <span className="gradient-text">without the rewatching</span>
            </h1>
            <p className="mt-4 max-w-xl text-base text-muted sm:text-lg">
              Use a YouTube playlist or recordings on your computer, choose the lectures, and ask anything. Answers stream
              in with citations that open the exact moment.
            </p>
          </>
        )}

        <div className="mt-8 inline-flex rounded-xl border border-line bg-surface p-1" role="tablist" aria-label="Lecture source">
          {[
            { id: 'youtube' as const, label: 'YouTube playlist', icon: Youtube },
            { id: 'folder' as const, label: 'Folder on this computer', icon: FolderOpen },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={kind === id}
              onClick={() => {
                setKind(id)
                setError('')
              }}
              className={`flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition
                ${kind === id ? 'bg-accent text-white shadow-sm' : 'text-muted hover:text-ink'}`}
            >
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="mt-4 w-full max-w-2xl">
          <div className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-2 shadow-lg shadow-black/5 transition focus-within:border-accent sm:flex-row">
            <div className="flex flex-1 items-center gap-2 px-3">
              {kind === 'youtube' ? <Link2 size={18} className="shrink-0 text-muted" /> : <Folder size={18} className="shrink-0 text-muted" />}
              <input
                key={kind}
                value={value}
                onChange={(e) => (kind === 'youtube' ? setUrl : setFolder)(e.target.value)}
                placeholder={kind === 'youtube' ? 'https://www.youtube.com/playlist?list=...' : 'C:\\Users\\you\\Videos\\My Course'}
                aria-label={kind === 'youtube' ? 'YouTube playlist link' : 'Folder path'}
                className="h-11 w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted/70"
              />
            </div>
            <button
              type="submit"
              disabled={loading || !value.trim()}
              className="flex h-11 items-center justify-center gap-2 rounded-xl bg-accent px-5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50"
            >
              {loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
              {loading ? (kind === 'youtube' ? 'Reading playlist...' : 'Scanning folder...') : kind === 'youtube' ? 'Load playlist' : 'Load folder'}
            </button>
          </div>
          {kind === 'folder' && (
            <div className="mt-3 flex flex-col items-center gap-2 text-xs text-muted sm:flex-row sm:justify-between">
              <span>Tip: in File Explorer, right-click the folder → “Copy as path”, then paste it here.</span>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} className="accent-[var(--accent)]" />
                Include subfolders
              </label>
            </div>
          )}
          {kind === 'folder' && (
            <p className="mt-2 text-xs text-muted">
              Audio and video files (mp3, wav, m4a, mp4, mkv, …) are transcribed with Whisper on your computer.
            </p>
          )}
          {error && <p className="mt-3 text-sm text-danger">{error}</p>}
        </form>

        {!adding && (
          <div className="mt-16 grid w-full gap-3 text-left sm:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <div key={title} className="rounded-2xl border border-line bg-surface/70 p-4">
                <div className="mb-3 grid size-9 place-items-center rounded-lg bg-accent-soft text-accent">
                  <Icon size={18} />
                </div>
                <div className="text-sm font-semibold">{title}</div>
                <div className="mt-1 text-sm text-muted">{text}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
