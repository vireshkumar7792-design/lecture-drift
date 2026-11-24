import { useEffect, useRef, useState } from 'react'
import { AudioLines } from 'lucide-react'

interface Props {
  lectureId: string
  kind: 'audio' | 'video'
  start: number // seconds
  nonce: number // changes on every citation click, even for the same moment
  title: string
}

/**
 * Plays a lecture file from this computer (served by /api/media with Range support) and jumps to the
 * cited moment. Jumping within the same lecture just seeks; it does not reload the file.
 */
export default function LocalPlayer({ lectureId, kind, start, nonce, title }: Props) {
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null)
  const [error, setError] = useState('')
  const src = `/api/media/${lectureId}`

  const seekAndPlay = () => {
    const element = media.current
    if (!element) return
    element.currentTime = start
    element.play().catch(() => {
      /* autoplay can be blocked until the student interacts; the controls are still there */
    })
  }

  // A new citation click: seek if the file is already loaded (a new file seeks once its metadata arrives).
  useEffect(() => {
    setError('')
    if (media.current && media.current.readyState >= 1 && media.current.currentSrc.endsWith(src)) seekAndPlay()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce])

  const onError = () =>
    setError('This file could not be played here. It may have been moved, or your browser cannot play this format (for example some .mkv files).')

  if (kind === 'audio')
    return (
      <div className="rounded-xl border border-line bg-gradient-to-br from-accent/15 to-accent-2/15 p-4">
        <div className="mb-3 flex items-center gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent text-white">
            <AudioLines size={22} />
          </div>
          <div className="min-w-0 text-sm font-medium">
            <div className="truncate">{title}</div>
            <div className="text-xs font-normal text-muted">Audio recording</div>
          </div>
        </div>
        <audio ref={media} src={src} controls preload="metadata" onLoadedMetadata={seekAndPlay} onError={onError} className="w-full" />
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </div>
    )

  return (
    <div>
      <div className="aspect-video overflow-hidden rounded-xl bg-black shadow-md">
        <video ref={media} src={src} controls preload="metadata" onLoadedMetadata={seekAndPlay} onError={onError} className="size-full" />
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  )
}
