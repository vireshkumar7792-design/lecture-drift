import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, Circle, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { isLocal, runJob, type Lecture, type ProgressUpdate, type StudyMode, type TranscriptResult } from '../api'
import LectureThumb from './LectureThumb'

interface Props {
  lectures: Lecture[] // already numbered in teaching order
  mode: StudyMode
  onBack: () => void
  onReady: (result: TranscriptResult) => void
}

type Stage = 'captions' | 'whisper' | 'index'
type Phase = Stage | 'done' | 'failed'

interface WhisperRow {
  lecture: Lecture
  stage: ProgressUpdate['stage'] | 'waiting'
  percent: number
  message: string
}

const STAGE_LABEL: Record<WhisperRow['stage'] & string, string> = {
  waiting: 'Waiting',
  downloading: 'Downloading audio',
  transcribing: 'Transcribing',
  done: 'Ready',
  failed: 'Failed',
}

export default function PrepareStep({ lectures, mode, onBack, onReady }: Props) {
  const [phase, setPhase] = useState<Phase>('captions')
  const [failedAt, setFailedAt] = useState<Stage>('captions')
  const [log, setLog] = useState<string[]>([])
  const [error, setError] = useState('')
  const [summary, setSummary] = useState<TranscriptResult | null>(null)
  const [rows, setRows] = useState<WhisperRow[]>([])
  const [attempt, setAttempt] = useState(0)
  const logEnd = useRef<HTMLDivElement>(null)

  const youtube = lectures.filter((l) => !isLocal(l))
  const local = lectures.filter(isLocal)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    const add = (update: ProgressUpdate) => {
      if (cancelled) return
      setLog((l) => [...l, update.message])
      if (update.lecture)
        setRows((all) =>
          all.map((row) =>
            row.lecture.video_id === update.lecture
              ? { ...row, stage: update.stage ?? row.stage, percent: update.percent ?? row.percent, message: update.message }
              : row,
          ),
        )
    }
    let stage: Stage = 'captions'

    const run = async () => {
      setLog([])
      setError('')
      setSummary(null)
      try {
        // 1. YouTube captions (lectures without captions come back as "deferred").
        stage = 'captions'
        setPhase('captions')
        const captions: TranscriptResult = youtube.length
          ? await runJob<TranscriptResult>('/api/transcripts', { lectures: youtube }, add, controller.signal)
          : { transcripts: [], deferred: [], errors: [] }
        if (cancelled) return

        // 2. Whisper for local files and caption-less YouTube lectures.
        const noCaptions = new Set(captions.deferred.map((d) => d.video_id))
        const toTranscribe = [...lectures.filter((l) => isLocal(l) || noCaptions.has(l.video_id))]
        let whisper: TranscriptResult = { transcripts: [], deferred: [], errors: [] }
        if (toTranscribe.length) {
          stage = 'whisper'
          setPhase('whisper')
          setRows(toTranscribe.map((lecture) => ({ lecture, stage: 'waiting', percent: 0, message: '' })))
          whisper = await runJob<TranscriptResult>('/api/transcribe', { lectures: toTranscribe }, add, controller.signal)
          if (cancelled) return
        }

        const result: TranscriptResult = {
          transcripts: [...captions.transcripts, ...whisper.transcripts].sort((a, b) => a.position - b.position),
          deferred: [],
          errors: [...captions.errors, ...whisper.errors],
        }
        setSummary(result)
        if (!result.transcripts.length) throw new Error('None of the chosen lectures could be transcribed.')
        if (mode === 'comparison' && result.transcripts.length < 2)
          throw new Error('Comparison needs at least two lectures with transcripts. Choose another lecture.')

        // 3. Search index.
        stage = 'index'
        setPhase('index')
        await runJob('/api/index', { video_ids: result.transcripts.map((t) => t.video_id) }, add, controller.signal)
        if (cancelled) return
        setPhase('done')
        setTimeout(() => !cancelled && onReady(result), result.errors.length ? 2500 : 600)
      } catch (e) {
        if (cancelled) return
        setFailedAt(stage)
        setPhase('failed')
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    run()
    return () => {
      cancelled = true
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])

  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would try to call
  // an implicitly returned Promise as the effect's cleanup function and crash the whole page.
  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: 'nearest' })
  }, [log])

  const order: Stage[] = ['captions', 'whisper', 'index']
  const stepState = (step: Stage) => {
    if (phase === 'failed') return step === failedAt ? 'failed' : order.indexOf(step) < order.indexOf(failedAt) ? 'done' : 'waiting'
    if (phase === 'done') return 'done'
    const now = order.indexOf(phase)
    const mine = order.indexOf(step)
    return mine < now ? 'done' : mine === now ? 'active' : 'waiting'
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-10">
        <h2 className="text-xl font-semibold tracking-tight">Preparing {lectures.length} lectures</h2>
        <p className="mt-1 text-sm text-muted">
          {youtube.length} from YouTube · {local.length} from this computer. Saved transcripts and indexes are reused.
        </p>

        <div className="mt-6 space-y-3">
          <StepRow state={stepState('captions')} title="Fetch YouTube captions" detail={youtube.length ? undefined : 'No YouTube lectures'} />
          <StepRow
            state={stepState('whisper')}
            title="Transcribe audio with Whisper"
            detail={rows.length ? `${rows.filter((r) => r.stage === 'done').length}/${rows.length} ready` : phase === 'captions' ? undefined : 'Nothing to transcribe'}
          >
            {rows.length > 0 && (
              <div className="mt-3 space-y-2">
                {rows.map((row) => (
                  <div key={row.lecture.video_id} className="flex items-center gap-3 rounded-lg bg-surface-2/60 p-2">
                    <div className="aspect-video w-14 shrink-0 overflow-hidden rounded bg-surface-2">
                      <LectureThumb lecture={row.lecture} className="size-full" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="truncate font-medium">
                          L{row.lecture.position} · {row.lecture.title}
                        </span>
                        <span className={`shrink-0 ${row.stage === 'failed' ? 'text-danger' : row.stage === 'done' ? 'text-success' : 'text-muted'}`}>
                          {STAGE_LABEL[row.stage ?? 'waiting']}
                          {(row.stage === 'downloading' || row.stage === 'transcribing') && ` ${row.percent}%`}
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${row.stage === 'failed' ? 'bg-danger' : row.stage === 'done' ? 'bg-success' : 'bg-accent'}`}
                          style={{ width: `${row.stage === 'done' ? 100 : row.stage === 'failed' ? 100 : row.percent}%` }}
                        />
                      </div>
                      {row.stage === 'failed' && <p className="mt-1 text-[11px] text-danger">{row.message.split(': Failed: ').pop()}</p>}
                    </div>
                  </div>
                ))}
                <p className="text-[11px] text-muted">Whisper runs on your CPU. You can keep this tab open and come back later.</p>
              </div>
            )}
          </StepRow>
          <StepRow state={stepState('index')} title="Build the search index" />
        </div>

        <div className="scroll-thin mt-6 max-h-48 overflow-y-auto rounded-xl border border-line bg-surface p-3 font-mono text-xs text-muted">
          {log.length ? log.map((line, i) => <div key={i} className="truncate py-0.5">{line}</div>) : <div>Starting...</div>}
          <div ref={logEnd} />
        </div>

        {summary && summary.errors.length > 0 && (
          <div className="mt-4 rounded-xl border border-warning/40 bg-warning/10 p-3 text-sm">
            <div className="flex items-center gap-2 font-medium text-warning">
              <AlertTriangle size={16} /> Some lectures were skipped
            </div>
            <ul className="mt-2 space-y-1 text-xs text-muted">
              {summary.errors.map((e) => (
                <li key={e.video_id ?? e.title}>
                  {e.title}: {e.error}
                </li>
              ))}
            </ul>
          </div>
        )}

        {phase === 'failed' && <div className="mt-4 rounded-xl border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</div>}

        <div className="mt-6 flex gap-2">
          <button type="button" onClick={onBack} className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm hover:bg-surface-2">
            <ArrowLeft size={15} /> Back to lectures
          </button>
          {phase === 'failed' && (
            <button
              type="button"
              onClick={() => setAttempt((a) => a + 1)}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:brightness-110"
            >
              <RefreshCw size={15} /> Try again
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function StepRow({ state, title, detail, children }: { state: string; title: string; detail?: string; children?: ReactNode }) {
  const icon = {
    done: <CheckCircle2 size={20} className="text-success" />,
    active: <Loader2 size={20} className="animate-spin text-accent" />,
    failed: <XCircle size={20} className="text-danger" />,
    waiting: <Circle size={20} className="text-line" />,
  }[state]
  return (
    <div className={`rounded-xl border bg-surface p-3.5 ${state === 'active' ? 'border-accent' : 'border-line'}`}>
      <div className="flex items-center gap-3">
        {icon}
        <span className={`text-sm font-medium ${state === 'waiting' ? 'text-muted' : ''}`}>{title}</span>
        {detail && <span className="ml-auto text-xs text-muted">{detail}</span>}
      </div>
      {children}
    </div>
  )
}
