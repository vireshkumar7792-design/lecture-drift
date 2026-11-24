import { FileAudio, FileVideo } from 'lucide-react'
import { isLocal, thumbnail, type Lecture } from '../api'

/** YouTube thumbnail, or a tile with a file icon for lectures stored on this computer. */
export default function LectureThumb({ lecture, className = '' }: { lecture: Pick<Lecture, 'video_id' | 'source' | 'kind' | 'file_name'>; className?: string }) {
  if (isLocal(lecture)) {
    const Icon = lecture.kind === 'video' ? FileVideo : FileAudio
    const extension = lecture.file_name?.split('.').pop()?.toUpperCase()
    return (
      <div className={`grid place-items-center bg-gradient-to-br from-accent/25 to-accent-2/25 text-accent ${className}`}>
        <div className="flex flex-col items-center gap-0.5">
          <Icon size={20} />
          {extension && <span className="text-[9px] font-semibold tracking-wide">{extension}</span>}
        </div>
      </div>
    )
  }
  return <img src={thumbnail(lecture.video_id)} alt="" loading="lazy" className={`object-cover ${className}`} />
}
