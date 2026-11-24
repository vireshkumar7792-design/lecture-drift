"""Transcribe lecture audio/video with Whisper: Groq's hosted Whisper, or faster-whisper on the CPU.

Produces the same transcript schema as youtube_transcripts.py, so the RAG chat works unchanged:
    {source: "whisper", language, is_generated: True, text, segments: [{start, end, text}]}
plus the lecture fields (video_id, lecture_source, title, position, url) added by transcribe_lectures.

Results are cached in data/transcripts/<lecture id>.json and also keyed by a fingerprint of the file's
content, so a file is transcribed once even if it is moved. Files are written atomically: a run that is
interrupted leaves no half-written transcript, and repeating it simply starts that lecture again.

Settings (environment or .env):
  WHISPER_BACKEND   "groq" or "local" (default: groq when GROQ_API_KEY is set, otherwise local)
  GROQ_MODEL        hosted model (default "whisper-large-v3")
  WHISPER_MODEL     local faster-whisper model (default "small")
  WHISPER_LANGUAGE  language code such as "hi" or "en" (default: detect)
  WHISPER_BEAM_SIZE local beam size (default 5; 1 is faster and slightly less accurate)

Groq: audio is re-encoded to 16 kHz mono Opus (about 11 MB per hour) before upload; if that is still over
the 25 MB upload limit it is split into ~10-minute chunks whose timestamps are shifted back into place.
Rate limits (HTTP 429) are waited out using the retry-after header; any other Groq failure falls back to
local faster-whisper for that lecture. API keys are only sent in the Authorization header, never logged.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

# On Windows with Anaconda, faster-whisper (ctranslate2) and numpy/torch each bring their own copy of
# Intel's OpenMP runtime, and the second copy aborts the process ("OMP: Error #15"). This is the
# standard workaround for that pairing; it must be set before either library starts.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

PROJECT_FOLDER = Path(__file__).resolve().parent
WHISPER_FOLDER = PROJECT_FOLDER / 'data' / 'transcripts'
FINGERPRINTS_PATH = WHISPER_FOLDER / 'fingerprints.json'

GROQ_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
GROQ_UPLOAD_LIMIT = 24 * 1024 * 1024  # free tier allows 25 MB per file; keep a safety margin
UPLOAD_BITRATE = 24_000  # bits per second: speech stays clear in Opus at this rate
CHUNK_SECONDS = 600
MAX_RATE_LIMIT_WAIT = 900  # seconds; a longer wait falls back to local Whisper instead
LANGUAGE_CODES = {'english': 'en', 'hindi': 'hi', 'urdu': 'ur', 'bengali': 'bn', 'tamil': 'ta', 'telugu': 'te',
                  'marathi': 'mr', 'gujarati': 'gu', 'kannada': 'kn', 'malayalam': 'ml', 'punjabi': 'pa'}

_model = None
_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()  # one transcription at a time: Whisper already uses every CPU core
_files_lock = threading.Lock()


def whisper_settings():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_FOLDER / '.env')
    except ImportError:
        pass
    has_groq_key = bool(os.environ.get('GROQ_API_KEY'))
    return {
        'backend': (os.environ.get('WHISPER_BACKEND') or ('groq' if has_groq_key else 'local')).strip().lower(),
        'has_groq_key': has_groq_key,
        'groq_model': os.environ.get('GROQ_MODEL', 'whisper-large-v3'),
        'model': os.environ.get('WHISPER_MODEL', 'small'),
        'language': os.environ.get('WHISPER_LANGUAGE') or None,
        'beam_size': int(os.environ.get('WHISPER_BEAM_SIZE', '5')),
    }


def transcription_backend():
    """Which backend new transcriptions will use, for the UI: {'backend', 'model'}."""
    settings = whisper_settings()
    if settings['backend'] == 'groq' and settings['has_groq_key']:
        return {'backend': 'groq', 'model': settings['groq_model']}
    return {'backend': 'local', 'model': settings['model']}


def get_whisper_model(progress=print):
    """Load the Whisper model once per process (the first run downloads it)."""
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise ValueError('Install Whisper support first: python -m pip install faster-whisper') from None
            name = whisper_settings()['model']
            progress(f'Loading the Whisper "{name}" model (the first run downloads it, this can take a few minutes)...')
            _model = WhisperModel(name, device='cpu', compute_type='int8')
    return _model


# ---------------------------------------------------------------- transcription

def transcribe_audio(path, progress=print):
    """Transcribe one audio/video file with the configured backend.

    progress(message, percent=...) reports % of the audio done. Groq failures other than an unreadable
    file fall back to local faster-whisper for this file.
    """
    settings = whisper_settings()
    if settings['backend'] == 'groq':
        if not settings['has_groq_key']:
            progress('WHISPER_BACKEND is "groq" but GROQ_API_KEY is not set; using local Whisper instead.')
        else:
            try:
                return transcribe_with_groq(path, settings, progress)
            except AudioReadError:
                raise  # a broken file fails locally too, so there is nothing to fall back to
            except Exception as error:
                progress(f'Groq transcription failed ({error}). Using local Whisper on this computer instead...')
    return transcribe_locally(path, settings, progress)


def transcribe_locally(path, settings, progress=print):
    """Transcribe with faster-whisper on the CPU."""
    model = get_whisper_model(progress)
    started = time.time()
    try:
        pieces, info = model.transcribe(str(path), language=settings['language'], vad_filter=True,
                                        beam_size=settings['beam_size'])
    except Exception:
        raise AudioReadError(path) from None

    duration = float(info.duration or 0)
    progress(f'Detected language: {info.language}. Transcribing {duration / 60:.0f} minutes of audio...', percent=0)
    segments, last_percent = [], 0
    # faster-whisper produces segments lazily, so progress can follow the audio position.
    for piece in pieces:
        text = ' '.join(piece.text.split())
        if text and piece.end > piece.start:
            segments.append({'start': round(float(piece.start), 2), 'end': round(float(piece.end), 2), 'text': text})
        percent = min(99, int(piece.end / duration * 100)) if duration else 0
        if percent > last_percent:
            last_percent = percent
            progress(f'Transcribing: {percent}% of the audio', percent=percent)

    if not segments:
        raise ValueError(f'Whisper found no speech in "{Path(path).name}".')
    return {
        'source': 'whisper',
        'language': info.language,
        'is_generated': True,
        'text': ' '.join(segment['text'] for segment in segments),
        'segments': segments,
        'whisper_backend': 'local',
        'whisper_model': settings['model'],
        'audio_seconds': round(duration, 1),
        'transcribe_seconds': round(time.time() - started, 1),
    }


class AudioReadError(ValueError):
    """The file cannot be decoded as audio (no backend can help, so there is no fallback)."""

    def __init__(self, path):
        super().__init__(f'Could not read "{Path(path).name}" as audio. The file may be damaged, '
                         'incomplete, or not a real audio/video file.')


# ---------------------------------------------------------------- Groq (hosted Whisper)

def encode_for_upload(path, folder, chunk_seconds=None):
    """Re-encode audio to 16 kHz mono Opus in `folder` with PyAV (no system ffmpeg needed).

    With chunk_seconds, start a new file every chunk_seconds of audio. Returns [(file, offset_seconds)].
    """
    import av

    try:
        source = av.open(str(path))
    except Exception:
        raise AudioReadError(path) from None
    chunks, state = [], {'output': None, 'stream': None}

    def finish():
        if state['output'] is not None:
            for packet in state['stream'].encode(None):  # flush the encoder
                state['output'].mux(packet)
            state['output'].close()
            state['output'] = None

    with source:
        audio = next((s for s in source.streams if s.type == 'audio'), None)
        if audio is None:
            raise AudioReadError(path)
        try:
            for frame in source.decode(audio):
                start = float(frame.time or 0)
                if state['output'] is None or (chunk_seconds and start >= chunks[-1][1] + chunk_seconds):
                    finish()
                    file = Path(folder) / f'part{len(chunks):03d}.ogg'
                    state['output'] = av.open(str(file), 'w', format='ogg')
                    state['stream'] = state['output'].add_stream('libopus', rate=16000, layout='mono')
                    state['stream'].bit_rate = UPLOAD_BITRATE
                    chunks.append((file, start if chunk_seconds else 0.0))
                frame.pts = None  # let the encoder number the resampled frames itself
                for packet in state['stream'].encode(frame):
                    state['output'].mux(packet)
        except av.FFmpegError:
            raise AudioReadError(path) from None
        finally:
            finish()
    return chunks


def _groq_error_message(response):
    try:
        return response.json()['error']['message'][:200]
    except Exception:
        return response.text[:200] or response.reason


def _retry_after_seconds(response):
    try:
        return max(1, int(float(response.headers.get('retry-after', '20'))) + 1)
    except ValueError:
        return 20


def _groq_transcribe_file(file, settings, progress):
    """Send one audio file to Groq; waits out rate limits (HTTP 429) using the retry-after header."""
    import requests

    data = {'model': settings['groq_model'], 'response_format': 'verbose_json', 'temperature': '0',
            'timestamp_granularities[]': 'segment'}
    if settings['language']:
        data['language'] = settings['language']
    for _ in range(5):
        with open(file, 'rb') as audio:
            response = requests.post(GROQ_URL, data=data, timeout=(20, 600),
                                     files={'file': (file.name, audio, 'audio/ogg')},
                                     headers={'Authorization': 'Bearer ' + os.environ['GROQ_API_KEY']})
        if response.status_code == 429:
            wait = _retry_after_seconds(response)
            if wait > MAX_RATE_LIMIT_WAIT:
                raise RuntimeError(f'Groq rate limit reached; it resets in about {wait // 60} minutes')
            progress(f'Groq rate limit reached. Waiting {wait} seconds before trying again...')
            time.sleep(wait)
            continue
        if response.status_code != 200:
            raise RuntimeError(f'Groq returned HTTP {response.status_code}: {_groq_error_message(response)}')
        return response.json()
    raise RuntimeError('Groq kept returning "rate limit reached" after several waits')


def transcribe_with_groq(path, settings, progress=print):
    """Transcribe with Groq's hosted Whisper; returns the same schema as transcribe_locally."""
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='lecture-upload-') as folder:
        progress('Preparing audio for upload (16 kHz mono)...', percent=0)
        chunks = encode_for_upload(path, folder)
        if chunks[0][0].stat().st_size > GROQ_UPLOAD_LIMIT:
            for file, _ in chunks:
                file.unlink()
            progress(f'Audio is too long for one upload; splitting it into {CHUNK_SECONDS // 60}-minute parts...')
            chunks = encode_for_upload(path, folder, CHUNK_SECONDS)

        segments, languages, duration = [], [], 0.0
        for number, (file, offset) in enumerate(chunks, start=1):
            part = f' (part {number} of {len(chunks)})' if len(chunks) > 1 else ''
            progress(f'Uploading to Groq{part}, {file.stat().st_size / 1e6:.1f} MB...',
                     percent=int((number - 1) / len(chunks) * 100))
            result = _groq_transcribe_file(file, settings, progress)
            languages.append(result.get('language') or '')
            duration = max(duration, offset + float(result.get('duration') or 0))
            for piece in result.get('segments') or []:
                text = ' '.join(str(piece.get('text', '')).split())
                start, end = float(piece['start']) + offset, float(piece['end']) + offset
                if text and end > start:
                    segments.append({'start': round(start, 2), 'end': round(end, 2), 'text': text})
        progress('Groq transcription received.', percent=99)

    if not segments:
        raise RuntimeError('Groq returned no speech segments')
    language = (max(set(languages), key=languages.count) or 'unknown').lower()
    return {
        'source': 'whisper',
        # Groq names the language ("Hindi"); local Whisper and YouTube use codes ("hi"). Keep codes.
        'language': LANGUAGE_CODES.get(language, language),
        'is_generated': True,
        'text': ' '.join(segment['text'] for segment in segments),
        'segments': segments,
        'whisper_backend': 'groq',
        'whisper_model': settings['groq_model'],
        'audio_seconds': round(duration, 1),
        'transcribe_seconds': round(time.time() - started, 1),
        'upload_parts': len(chunks),
    }


# ---------------------------------------------------------------- cache

def file_fingerprint(path):
    """Fast content identity: file size plus hashes of its first and last 4 MB."""
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode())
    with path.open('rb') as file:
        digest.update(file.read(4 * 1024 * 1024))
        if size > 8 * 1024 * 1024:
            file.seek(-4 * 1024 * 1024, os.SEEK_END)
            digest.update(file.read())
    return digest.hexdigest()


def _write_json_atomic(path, data):
    """Write to a temporary file and swap it in, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_whisper_transcript(lecture_id):
    """Return a finished Whisper transcript for this lecture id, or None."""
    data = _read_json(WHISPER_FOLDER / f'{lecture_id}.json', None)
    if (isinstance(data, dict) and data.get('source') == 'whisper' and data.get('video_id') == lecture_id
            and data.get('segments') and data.get('text')):
        return data
    return None


def _cached_by_fingerprint(fingerprint):
    with _files_lock:
        other_id = _read_json(FINGERPRINTS_PATH, {}).get(fingerprint)
    return load_whisper_transcript(other_id) if other_id else None


def _save(transcript, fingerprint=None):
    with _files_lock:
        _write_json_atomic(WHISPER_FOLDER / f'{transcript["video_id"]}.json', transcript)
        if fingerprint:
            fingerprints = _read_json(FINGERPRINTS_PATH, {})
            fingerprints[fingerprint] = transcript['video_id']
            _write_json_atomic(FINGERPRINTS_PATH, fingerprints)


def _with_lecture_fields(transcript, lecture, source):
    """Add the same lecture fields the YouTube caption path adds."""
    video_id = lecture['video_id']
    return dict(transcript) | {
        'video_id': video_id,
        'lecture_source': source,
        'title': lecture.get('title') or 'Untitled lecture',
        'position': lecture.get('position', 0),
        'url': 'https://www.youtube.com/watch?v=' + video_id if source == 'youtube' else '',
    }


def transcribe_file_cached(lecture, media_path, source, progress=print):
    """Transcribe one lecture's media file unless a cached transcript already exists."""
    cached = load_whisper_transcript(lecture['video_id'])
    if cached:
        progress('Using the saved Whisper transcript.', percent=100)
        transcript = _with_lecture_fields(cached, lecture, source)
        if transcript != cached:  # title/position changed: keep the file current, like the caption cache
            _save(transcript)
        return transcript

    fingerprint = file_fingerprint(media_path)
    same_file = _cached_by_fingerprint(fingerprint)
    if same_file:
        progress('This file was transcribed before under another name; reusing that transcript.', percent=100)
        transcript = _with_lecture_fields(same_file, lecture, source)
    else:
        if _transcribe_lock.locked():
            progress('Waiting for another transcription to finish...')
        with _transcribe_lock:
            transcript = _with_lecture_fields(transcribe_audio(media_path, progress), lecture, source)
    transcript['fingerprint'] = fingerprint
    _save(transcript, fingerprint)
    return transcript


# ---------------------------------------------------------------- lectures from any source

def transcribe_lectures(lectures, progress=print):
    """Transcribe local files and caption-less YouTube lectures. One failure never stops the rest.

    Returns {'transcripts', 'deferred', 'errors'} like youtube_transcripts.fetch_transcripts.
    """
    from local_media import lecture_media_path
    from youtube_transcripts import download_audio, forget_deferred, mark_deferred

    transcripts, errors = [], []
    for number, lecture in enumerate(lectures, start=1):
        title = lecture.get('title') or 'Untitled lecture'
        source = lecture.get('source', 'youtube')
        prefix = f'{number}/{len(lectures)}: {title}'

        def report(message, percent=None, stage='transcribing'):
            progress(f'{prefix}: {message}', lecture=lecture['video_id'], stage=stage, percent=percent)

        try:
            if source == 'local':
                media_path = lecture_media_path(lecture['video_id'])
            elif load_whisper_transcript(lecture['video_id']):
                media_path = None  # already transcribed: no need to download the audio again
            else:
                media_path = download_audio(
                    lecture['video_id'],
                    lambda message, percent=None: report(message, percent, stage='downloading'))
            # A cached transcript is returned before the (possibly missing) media file is touched.
            transcript = transcribe_file_cached(lecture, media_path, source, report)
            transcripts.append(transcript)
            if source == 'youtube':
                forget_deferred(lecture['video_id'])
            report('Transcript ready.', percent=100, stage='done')
        except Exception as error:
            message = str(error) or error.__class__.__name__
            errors.append({'video_id': lecture['video_id'], 'title': title, 'error': message})
            if source == 'youtube':
                mark_deferred(lecture, f'Audio transcription failed: {message}')
            report(f'Failed: {message}', stage='failed')
    return {'transcripts': transcripts, 'deferred': [], 'errors': errors}
