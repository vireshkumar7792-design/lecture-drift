import { useMemo, useState, type DragEvent } from 'react'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  AudioLines,
  Check,
  Folder,
  GitCompareArrows,
  GripVertical,
  Library,
  Plus,
  Search,
  X,
  Youtube,
} from 'lucide-react'
import { formatSize, isLocal, type Lecture, type Playlist, type StudyMode } from '../api'
import LectureThumb from './LectureThumb'

interface Props {
  sources: Playlist[]
  pool: Lecture[] // every lecture from every source, in the student's order
  chosen: Lecture[] // lectures that will be studied, numbered 1..n in teaching order
  selected: string[]
  mode: StudyMode
  concept: string
  onChange: (changes: { selected?: string[]; mode?: StudyMode; concept?: string; order?: string[] }) => void
  onAddSource: () => void
  onRemoveSource: (url: string) => void
  onStart: () => void
}

const MODES: { id: StudyMode; icon: typeof Library; title: string; text: string }[] = [
  { id: 'comparison', icon: GitCompareArrows, title: 'Compare lectures', text: 'Pick 2+ lectures and see how each one explains a concept.' },
  { id: 'reference', icon: Library, title: 'All lectures', text: 'Use every lecture from your sources as a searchable reference.' },
]

export default function SelectStep({ sources, pool, chosen, selected, mode, concept, onChange, onAddSource, onRemoveSource, onStart }: Props) {
  const [filter, setFilter] = useState('')
  const [dragFrom, setDragFrom] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)
  const visible = useMemo(() => pool.filter((l) => l.title.toLowerCase().includes(filter.trim().toLowerCase())), [pool, filter])

  const comparing = mode === 'comparison'
  const canStart = comparing ? chosen.length >= 2 : chosen.length > 0
  const needWhisper = chosen.filter(isLocal).length

  const toggle = (videoId: string) =>
    onChange({ selected: selected.includes(videoId) ? selected.filter((id) => id !== videoId) : [...selected, videoId] })
  const selectVisible = () => onChange({ selected: [...new Set([...selected, ...visible.map((l) => l.video_id)])] })

  /** Move a chosen lecture within the teaching order; unchosen lectures keep their places. */
  const move = (from: number, to: number) => {
    if (to < 0 || to >= chosen.length || from === to) return
    const ids = chosen.map((l) => l.video_id)
    const [moved] = ids.splice(from, 1)
    ids.splice(to, 0, moved)
    const chosenIds = new Set(ids)
    let next = 0
    onChange({ order: pool.map((l) => (chosenIds.has(l.video_id) ? ids[next++] : l.video_id)) })
  }

  const onDrop = (event: DragEvent, index: number) => {
    event.preventDefault()
    if (dragFrom !== null) move(dragFrom, index)
    setDragFrom(null)
    setDragOver(null)
  }

  return (
    <div className="mx-auto grid h-full max-w-[1600px] gap-6 overflow-y-auto px-4 py-6 lg:grid-cols-[1fr_380px] lg:overflow-hidden">
      {/* Lecture pool */}
      <section className="flex min-h-0 flex-col">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight">
              {sources.length === 1 ? sources[0].title : `${sources.length} lecture sources`}
            </h2>
            <p className="mt-1 text-sm text-muted">{pool.length} lectures available</p>
          </div>
          <button
            type="button"
            onClick={onAddSource}
            className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium transition hover:border-accent"
          >
            <Plus size={15} /> Add another source
          </button>
        </div>

        <div className="mb-3 flex flex-wrap gap-2">
          {sources.map((source) => (
            <span key={source.url} title={source.url} className="inline-flex max-w-xs items-center gap-1.5 rounded-full border border-line bg-surface py-1 pr-1 pl-3 text-xs">
              {source.source === 'local' ? <Folder size={12} className="text-accent" /> : <Youtube size={12} className="text-danger" />}
              <span className="truncate">{source.title}</span>
              <span className="text-muted">· {source.lectures.length}</span>
              <button
                type="button"
                onClick={() => onRemoveSource(source.url)}
                title="Remove this source"
                className="rounded-full p-0.5 text-muted hover:bg-surface-2 hover:text-danger"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-surface px-3 focus-within:border-accent">
            <Search size={15} className="text-muted" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter lectures" className="w-full bg-transparent text-sm outline-none" />
          </label>
          {comparing && (
            <>
              <button type="button" onClick={selectVisible} className="h-9 rounded-lg border border-line px-3 text-xs font-medium hover:bg-surface-2">
                Select {filter ? 'shown' : 'all'}
              </button>
              <button
                type="button"
                onClick={() => onChange({ selected: [] })}
                disabled={!selected.length}
                className="h-9 rounded-lg border border-line px-3 text-xs font-medium hover:bg-surface-2 disabled:opacity-40"
              >
                Clear
              </button>
            </>
          )}
        </div>

        <div className="scroll-thin grid min-h-0 flex-1 auto-rows-max gap-3 overflow-y-auto pr-1 pb-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((lecture) => {
            const isOn = !comparing || selected.includes(lecture.video_id)
            const local = isLocal(lecture)
            return (
              <button
                key={lecture.video_id}
                type="button"
                onClick={() => comparing && toggle(lecture.video_id)}
                aria-pressed={isOn}
                className={`group flex gap-3 rounded-xl border bg-surface p-2.5 text-left transition
                  ${comparing ? 'cursor-pointer hover:border-accent/60' : 'cursor-default'}
                  ${isOn && comparing ? 'border-accent ring-2 ring-accent/20' : 'border-line'}`}
              >
                <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-lg bg-surface-2">
                  <LectureThumb lecture={lecture} className="size-full" />
                  {!local && (
                    <span className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 text-[10px] font-semibold text-white">#{lecture.position}</span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-2 text-[13px] leading-snug font-medium">{lecture.title}</p>
                  <p className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                    {local ? (
                      <>
                        <AudioLines size={11} /> Local {lecture.kind ?? 'file'}
                        {lecture.size ? ` · ${formatSize(lecture.size)}` : ''}
                      </>
                    ) : (
                      <>
                        <Youtube size={11} /> YouTube
                      </>
                    )}
                  </p>
                </div>
                {comparing && (
                  <span
                    className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border transition
                      ${isOn ? 'border-accent bg-accent text-white' : 'border-line group-hover:border-accent/60'}`}
                  >
                    {isOn && <Check size={13} strokeWidth={3} />}
                  </span>
                )}
              </button>
            )
          })}
          {!visible.length && <p className="col-span-full py-10 text-center text-sm text-muted">No lecture matches “{filter}”.</p>}
        </div>
      </section>

      {/* Study options and teaching order */}
      <aside className="scroll-thin lg:overflow-y-auto">
        <div className="space-y-5 rounded-2xl border border-line bg-surface p-5">
          <div>
            <h3 className="mb-2 text-sm font-semibold">How do you want to study?</h3>
            <div className="grid gap-2">
              {MODES.map(({ id, icon: Icon, title, text }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => onChange({ mode: id })}
                  className={`flex items-start gap-3 rounded-xl border p-3 text-left transition
                    ${mode === id ? 'border-accent bg-accent-soft' : 'border-line hover:bg-surface-2'}`}
                >
                  <Icon size={18} className={mode === id ? 'mt-0.5 text-accent' : 'mt-0.5 text-muted'} />
                  <span>
                    <span className="block text-sm font-medium">{title}</span>
                    <span className="block text-xs text-muted">{text}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-baseline justify-between">
              <h3 className="text-sm font-semibold">Teaching order</h3>
              <span className="text-xs text-muted">{chosen.length} lectures</span>
            </div>
            {chosen.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line p-4 text-center text-xs text-muted">Select lectures on the left. Drag them here into the order you want to learn them.</p>
            ) : (
              <>
                <ol className="scroll-thin max-h-72 space-y-1 overflow-y-auto pr-1">
                  {chosen.map((lecture, index) => (
                    <li
                      key={lecture.video_id}
                      draggable
                      onDragStart={() => setDragFrom(index)}
                      onDragOver={(e) => {
                        e.preventDefault()
                        setDragOver(index)
                      }}
                      onDragLeave={() => setDragOver((o) => (o === index ? null : o))}
                      onDrop={(e) => onDrop(e, index)}
                      onDragEnd={() => {
                        setDragFrom(null)
                        setDragOver(null)
                      }}
                      className={`group flex items-center gap-2 rounded-lg border bg-surface-2/60 p-1.5 text-xs transition
                        ${dragOver === index && dragFrom !== index ? 'border-accent' : 'border-transparent'}
                        ${dragFrom === index ? 'opacity-40' : ''}`}
                    >
                      <GripVertical size={14} className="shrink-0 cursor-grab text-muted" aria-hidden />
                      <span className="w-7 shrink-0 font-mono font-semibold text-accent">L{lecture.position}</span>
                      <span className="min-w-0 flex-1 truncate" title={lecture.title}>
                        {lecture.title}
                      </span>
                      {isLocal(lecture) ? <AudioLines size={12} className="shrink-0 text-muted" /> : <Youtube size={12} className="shrink-0 text-muted" />}
                      <span className="flex shrink-0">
                        <button type="button" onClick={() => move(index, index - 1)} disabled={index === 0} title="Move up" className="rounded p-0.5 text-muted hover:bg-surface hover:text-ink disabled:opacity-25">
                          <ArrowUp size={13} />
                        </button>
                        <button
                          type="button"
                          onClick={() => move(index, index + 1)}
                          disabled={index === chosen.length - 1}
                          title="Move down"
                          className="rounded p-0.5 text-muted hover:bg-surface hover:text-ink disabled:opacity-25"
                        >
                          <ArrowDown size={13} />
                        </button>
                      </span>
                    </li>
                  ))}
                </ol>
                <p className="mt-1.5 text-[11px] text-muted">Drag or use the arrows. This order sets the lecture numbers (L1, L2, …) used in answers.</p>
              </>
            )}
            {comparing && chosen.length < 2 && <p className="mt-2 text-xs text-warning">Select at least two lectures to compare.</p>}
          </div>

          <div>
            <label htmlFor="concept" className="mb-2 block text-sm font-semibold">
              What are you struggling with? <span className="font-normal text-muted">(optional)</span>
            </label>
            <textarea
              id="concept"
              value={concept}
              onChange={(e) => onChange({ concept: e.target.value })}
              rows={3}
              placeholder="e.g. I get what an LLM is, but agents with tools and memory confuse me."
              className="w-full resize-none rounded-xl border border-line bg-surface-2/50 p-3 text-sm outline-none focus:border-accent"
            />
          </div>

          {needWhisper > 0 && (
            <p className="rounded-xl bg-accent-soft p-3 text-xs text-ink/80">
              <strong className="text-accent">{needWhisper} local {needWhisper === 1 ? 'file' : 'files'}</strong> will be transcribed with Whisper on your
              CPU. The first time this takes a while (roughly a quarter of the recording length); after that it is instant.
            </p>
          )}

          <button
            type="button"
            disabled={!canStart}
            onClick={onStart}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-accent text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-40"
          >
            Prepare lectures <ArrowRight size={16} />
          </button>
          <p className="text-center text-[11px] text-muted">Gets transcripts and builds a search index. Saved work is reused.</p>
        </div>
      </aside>
    </div>
  )
}
