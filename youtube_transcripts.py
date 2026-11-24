"""Get existing YouTube captions; lectures without captions are deferred, then transcribed from audio.

Audio download (download_audio) and the deferred list helpers are used by audio_transcripts.py.
"""

import json
import threading
from pathlib import Path

from lecture_ids import is_youtube_id


TRANSCRIPT_FOLDER = Path(__file__).resolve().parent / 'data' / 'youtube_transcripts'
AUDIO_FOLDER = TRANSCRIPT_FOLDER.parent / 'audio'
DEFERRED_PATH = TRANSCRIPT_FOLDER.parent / 'deferred_lectures.json'
_deferred_lock = threading.Lock()


def get_video_transcript(video_id):
    """Return timestamped captions, or None when this lecture has no captions."""
    from youtube_transcript_api import (YouTubeTranscriptApi, NoTranscriptFound,
                                        TranscriptsDisabled, VideoUnavailable, RequestBlocked)

    if not is_youtube_id(video_id):
        raise ValueError('Invalid YouTube video ID.')

    try:
        available = YouTubeTranscriptApi().list(video_id)
        try:
            # The library prefers manual captions when available in these languages.
            selected = available.find_transcript(['en', 'hi'])
        except NoTranscriptFound:
            # Use another available language when English and Hindi are missing.
            selected = next(iter(available), None)
        if selected is None:
            return None
        captions = selected.fetch()
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except VideoUnavailable:
        raise ValueError('This YouTube video is unavailable.') from None
    except RequestBlocked:
        raise ValueError('YouTube blocked this caption request. Wait and try again later.') from None

    segments = []
    for caption in captions:
        text = ' '.join(caption.text.split())
        if text and caption.duration > 0:
            segments.append({
                'start': float(caption.start),
                'end': float(caption.start + caption.duration),
                'text': text,
            })
    if not segments:
        return None

    return {
        'source': 'youtube_captions',
        'language': captions.language_code,
        'is_generated': captions.is_generated,
        'text': ' '.join(segment['text'] for segment in segments),
        'segments': segments,
    }


def fetch_transcripts(lectures, progress=print):
    """Process available captions and keep a list of lectures to handle later."""
    if not lectures:
        raise ValueError('Select at least one lecture first.')
    TRANSCRIPT_FOLDER.mkdir(parents=True, exist_ok=True)
    transcripts = []
    deferred = []
    errors = []

    # Keep deferred videos between sessions, including videos from earlier selections.
    deferred_path = DEFERRED_PATH
    saved_deferred = {}
    if deferred_path.is_file():
        try:
            saved_deferred = json.loads(deferred_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            saved_deferred = {}
        if not isinstance(saved_deferred, dict):
            saved_deferred = {}

    for number, lecture in enumerate(lectures, start=1):
        title = lecture.get('title', 'Untitled lecture')
        if lecture.get('source', 'youtube') == 'local':
            # Local files have no captions; Whisper transcribes them in a later step.
            deferred.append({'video_id': lecture.get('video_id', ''), 'title': title, 'source': 'local',
                             'position': lecture.get('position', number), 'url': '',
                             'reason': 'Local file: it will be transcribed with Whisper in a later step.'})
            progress(f'{number}/{len(lectures)}: {title} is a local file, waiting for Whisper transcription')
            continue
        try:
            video_id = lecture['video_id']
            if not is_youtube_id(video_id):
                raise ValueError('Invalid YouTube video ID.')
            transcript_path = TRANSCRIPT_FOLDER / (video_id + '.json')
            transcript = None

            if transcript_path.is_file():
                try:
                    saved = json.loads(transcript_path.read_text(encoding='utf-8'))
                except json.JSONDecodeError:
                    saved = {}
                if (isinstance(saved, dict) and saved.get('source') == 'youtube_captions'
                        and saved.get('video_id') == video_id and saved.get('text')
                        and saved.get('segments') and saved.get('language')
                        and isinstance(saved.get('is_generated'), bool)):
                    transcript = saved

            if transcript is None:
                progress(f'{number}/{len(lectures)}: fetching YouTube captions for {title}')
                transcript = get_video_transcript(video_id)
            else:
                progress(f'{number}/{len(lectures)}: using saved captions for {title}')

            if transcript is None:
                # No captions, but Whisper may have transcribed the audio in an earlier run.
                from audio_transcripts import load_whisper_transcript, transcribe_file_cached
                if load_whisper_transcript(video_id):
                    transcripts.append(transcribe_file_cached(
                        dict(lecture, title=title), None, 'youtube', lambda message, **_: None))
                    saved_deferred.pop(video_id, None)
                    progress(f'{number}/{len(lectures)}: using the saved Whisper transcript for {title}')
                    continue
                deferred_lecture = {
                    'video_id': video_id,
                    'title': title,
                    'source': 'youtube',
                    'position': lecture.get('position', number),
                    'url': 'https://www.youtube.com/watch?v=' + video_id,
                    'reason': 'No YouTube captions are available. Handle this lecture in a later stage.',
                }
                deferred.append(deferred_lecture)
                saved_deferred[video_id] = deferred_lecture
                progress(f'{number}/{len(lectures)}: deferred {title} because captions are unavailable')
                continue

            transcript.update({
                'video_id': video_id,
                'title': title,
                'position': lecture.get('position', number),
                'url': 'https://www.youtube.com/watch?v=' + video_id,
            })
            transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding='utf-8')
            transcripts.append(transcript)
            # A previously deferred video may have gained captions since the last request.
            saved_deferred.pop(video_id, None)
        except ImportError:
            raise ValueError('Install captions support: python -m pip install youtube-transcript-api') from None
        except Exception as error:
            errors.append({'title': title, 'error': str(error)})
            progress(f'Could not fetch captions for {title}. Continuing with the remaining lectures.')

    deferred_path.write_text(json.dumps(saved_deferred, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'transcripts': transcripts, 'deferred': deferred, 'errors': errors}


# ---------------------------------------------------------------- audio for lectures without captions

def download_audio(video_id, progress=print):
    """Download only the audio track of a YouTube lecture into data/audio/ and return its path.

    An earlier download is reused. progress(message, percent=...) reports the download percentage.
    """
    if not is_youtube_id(video_id):
        raise ValueError('Invalid YouTube video ID.')
    AUDIO_FOLDER.mkdir(parents=True, exist_ok=True)
    finished = [path for path in AUDIO_FOLDER.glob(video_id + '.*')
                if path.suffix not in ('.part', '.ytdl') and path.stat().st_size > 0]
    if finished:
        progress('Using the audio downloaded earlier.', percent=100)
        return finished[0]

    from yt_dlp import YoutubeDL
    reported = {'percent': -1}

    def hook(status):
        total = status.get('total_bytes') or status.get('total_bytes_estimate')
        if status.get('status') == 'downloading' and total:
            percent = int(status.get('downloaded_bytes', 0) * 100 / total)
            if percent >= reported['percent'] + 5:  # every 5% is enough
                reported['percent'] = percent
                progress(f'Downloading audio: {percent}%', percent=percent)

    options = {
        'format': 'bestaudio[ext=m4a]/bestaudio',  # audio only; Whisper reads m4a/webm directly
        'outtmpl': str(AUDIO_FOLDER / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'progress_hooks': [hook],
        'retries': 3,
        'socket_timeout': 30,
    }
    progress('Downloading audio...', percent=0)
    try:
        with YoutubeDL(options) as youtube:
            info = youtube.extract_info('https://www.youtube.com/watch?v=' + video_id, download=True)
            path = Path(youtube.prepare_filename(info))
    except Exception as error:
        reason = str(error).replace('ERROR: ', '').strip().splitlines()[0][:200]
        raise ValueError(f'Could not download the audio: {reason}') from None
    if not path.is_file():
        raise ValueError('The audio download finished but the file is missing.')
    progress('Audio downloaded.', percent=100)
    return path


def _read_deferred():
    try:
        data = json.loads(DEFERRED_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def forget_deferred(video_id):
    """Remove a lecture from data/deferred_lectures.json once it has a transcript."""
    with _deferred_lock:
        deferred = _read_deferred()
        if deferred.pop(video_id, None) is not None:
            DEFERRED_PATH.write_text(json.dumps(deferred, indent=2, ensure_ascii=False), encoding='utf-8')


def mark_deferred(lecture, reason):
    """Keep a lecture in the deferred list with a clear reason (for example a failed download)."""
    video_id = lecture['video_id']
    with _deferred_lock:
        deferred = _read_deferred()
        deferred[video_id] = {
            'video_id': video_id,
            'title': lecture.get('title') or 'Untitled lecture',
            'source': 'youtube',
            'position': lecture.get('position', 0),
            'url': 'https://www.youtube.com/watch?v=' + video_id,
            'reason': reason,
        }
        DEFERRED_PATH.write_text(json.dumps(deferred, indent=2, ensure_ascii=False), encoding='utf-8')
