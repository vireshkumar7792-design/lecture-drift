import { Check, GraduationCap, RotateCcw } from 'lucide-react'
import type { Status } from '../api'

export type Step = 'playlist' | 'select' | 'prepare' | 'study'

const STEPS: { id: Step; label: string }[] = [
  { id: 'playlist', label: 'Playlist' },
  { id: 'select', label: 'Lectures' },
  { id: 'prepare', label: 'Prepare' },
  { id: 'study', label: 'Study' },
]

interface Props {
  step: Step
  status: Status | null
  canGoTo: Record<Step, boolean>
  onStep: (step: Step) => void
  onReset: () => void
}

export default function Header({ step, status, canGoTo, onStep, onReset }: Props) {
  const current = STEPS.findIndex((s) => s.id === step)

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-4">
        <div className="flex items-center gap-2.5">
          <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-accent-2 text-white shadow-sm">
            <GraduationCap size={18} />
          </div>
          <div className="hidden leading-tight sm:block">
            <div className="text-sm font-semibold">Lecture Drift</div>
            <div className="text-[11px] text-muted">Learn from playlists, faster</div>
          </div>
        </div>

        <nav className="mx-auto flex items-center gap-1" aria-label="Progress">
          {STEPS.map((s, i) => {
            const done = i < current
            const active = i === current
            const enabled = canGoTo[s.id] && !active
            return (
              <div key={s.id} className="flex items-center">
                {i > 0 && <div className={`mx-1 h-px w-4 sm:w-8 ${i <= current ? 'bg-accent' : 'bg-line'}`} />}
                <button
                  type="button"
                  disabled={!enabled}
                  onClick={() => onStep(s.id)}
                  className={`flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium transition
                    ${active ? 'bg-accent-soft text-accent' : done ? 'text-ink' : 'text-muted'}
                    ${enabled ? 'cursor-pointer hover:bg-surface-2' : 'cursor-default'}`}
                >
                  <span
                    className={`grid size-5 place-items-center rounded-full text-[10px] font-semibold
                      ${active || done ? 'bg-accent text-white' : 'border border-line'}`}
                  >
                    {done ? <Check size={12} strokeWidth={3} /> : i + 1}
                  </span>
                  <span className="hidden md:inline">{s.label}</span>
                </button>
              </div>
            )
          })}
        </nav>

        <div className="flex items-center gap-2">
          {status && (
            <span
              title={status.llm_ready ? `Answers by ${status.llm_model}` : 'Add OPENROUTER_API_KEY to .env'}
              className="hidden items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[11px] text-muted lg:flex"
            >
              <span className={`size-1.5 rounded-full ${status.llm_ready ? 'bg-success' : 'bg-warning'}`} />
              {status.llm_ready ? status.llm_model?.split('/').pop() : 'No API key'}
            </span>
          )}
          <button
            type="button"
            onClick={onReset}
            title="Start a new session"
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted transition hover:bg-surface-2 hover:text-ink"
          >
            <RotateCcw size={14} />
            <span className="hidden sm:inline">New</span>
          </button>
        </div>
      </div>
    </header>
  )
}
