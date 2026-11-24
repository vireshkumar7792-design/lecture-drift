"""FastAPI backend for the Lecture Drift Detector web app.

Run:  python server.py   (or: uvicorn server:app --reload --port 8000)
Lectures come from a YouTube playlist, a folder on this computer, or both. YouTube captions are used
when they exist; local files and caption-less videos are transcribed with Whisper.
The React app (frontend/) talks to these endpoints. Long jobs and chat answers are streamed as
newline-delimited JSON (one event per line) so the UI can show progress and tokens as they arrive.
"""

import json
import queue
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

import rag_pipeline
from audio_transcripts import WHISPER_FOLDER, transcribe_lectures
from lecture_ids import LECTURE_ID_PATTERN, is_local_id, source_of
from local_media import lecture_media_path, scan_folder
from playlist import get_playlist_url, load_playlist
from youtube_transcripts import TRANSCRIPT_FOLDER, fetch_transcripts

app = FastAPI(title='Lecture Drift Detector')


# ---------------------------------------------------------------- request models

class PlaylistRequest(BaseModel):
    url: str


class FolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    recursive: bool = False


LectureId = Annotated[str, Field(pattern=LECTURE_ID_PATTERN)]


class Lecture(BaseModel):
    position: int
    video_id: LectureId
    title: str
    source: Literal['youtube', 'local'] = 'youtube'
    url: str = ''  # YouTube watch link; empty for local files

    @model_validator(mode='after')
    def id_matches_source(self):
        if source_of(self.video_id) != self.source:
            raise ValueError(f'Lecture "{self.title}" has an id that does not match its source ({self.source}).')
        return self


class TranscriptRequest(BaseModel):
    lectures: list[Lecture] = Field(min_length=1)


class IndexRequest(BaseModel):
    video_ids: list[LectureId] = Field(min_length=1)


class ChatTurn(BaseModel):
    role: Literal['user', 'assistant']
    content: str


class ChatRequest(BaseModel):
    video_ids: list[LectureId] = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal['comparison', 'reference'] = 'reference'
    concept: str = ''
    history: list[ChatTurn] = []


# ---------------------------------------------------------------- helpers

def ndjson(events):
    """Wrap an event generator as a streaming NDJSON response. Errors become an 'error' event."""
    def body():
        try:
            for event in events:
                yield json.dumps(event, ensure_ascii=False) + '\n'
        except Exception as error:  # report to the UI instead of cutting the stream silently
            yield json.dumps({'type': 'error', 'message': str(error)}, ensure_ascii=False) + '\n'
    return StreamingResponse(body(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def run_with_progress(job):
    """Run job(progress) in a thread, yielding progress events and finally the result."""
    messages = queue.Queue()
    outcome = {}

    def progress(text, **details):
        # details (lecture, stage, percent) let the UI draw per-lecture progress bars.
        messages.put({'message': str(text)} | {k: v for k, v in details.items() if v is not None})

    def worker():
        try:
            outcome['result'] = job(progress)
        except Exception as error:
            outcome['error'] = error
        finally:
            messages.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while (update := messages.get()) is not None:
        yield {'type': 'progress'} | update
    if 'error' in outcome:
        raise outcome['error']
    yield {'type': 'result', 'data': outcome['result']}


def load_transcripts(video_ids):
    """Load saved transcripts: YouTube captions first, then Whisper transcripts."""
    transcripts = []
    for video_id in dict.fromkeys(video_ids):
        for folder in (TRANSCRIPT_FOLDER, WHISPER_FOLDER):
            path = folder / f'{video_id}.json'
            if path.is_file():
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('segments'):
                    transcripts.append(data)
                    break
    return sorted(transcripts, key=lambda t: t.get('position', 0))


def summarize_transcript(transcript):
    """What the UI needs about a transcript: lecture metadata and size, not the full text."""
    return ({key: transcript[key] for key in ['video_id', 'title', 'position', 'url', 'language', 'is_generated']}
            | {'source': transcript.get('lecture_source', 'youtube'), 'text_source': transcript['source'],
               'segments': len(transcript['segments']), 'duration': transcript['segments'][-1]['end']})


# Keep a few built indexes in memory so switching between study sessions stays fast.
_indexes = OrderedDict()
_index_lock = threading.Lock()


def forget_indexes(video_ids):
    """Drop in-memory indexes that include these lectures (their title/position may have changed)."""
    changed = set(video_ids)
    with _index_lock:
        for key in [key for key in _indexes if changed & set(key)]:
            del _indexes[key]


def get_index(video_ids, progress=lambda text, **details: None):
    key = tuple(sorted(set(video_ids)))
    with _index_lock:
        if key in _indexes:
            _indexes.move_to_end(key)
            return _indexes[key]
        transcripts = load_transcripts(key)
        if not transcripts:
            raise ValueError('None of these lectures has a saved transcript yet. Fetch transcripts first.')
        progress('Loading the embedding model (first run downloads it)...')
        rag_pipeline.get_embedding_model()
        index = rag_pipeline.LectureIndex(transcripts, progress=progress)
        _indexes[key] = index
        while len(_indexes) > 4:
            _indexes.popitem(last=False)
        return index


def public_source(source):
    return {key: source[key] for key in ['label', 'link', 'source', 'video_id', 'position', 'title', 'start', 'end']} | {
        'text': source['text'][:1500]}


# ---------------------------------------------------------------- endpoints

@app.get('/api/status')
def status():
    _, _, model = rag_pipeline.llm_settings()
    return {'llm_ready': rag_pipeline.llm_available(), 'llm_model': model,
            'embedding_model': rag_pipeline.EMBEDDING_MODEL}


@app.post('/api/playlist')
def playlist(request: PlaylistRequest):
    try:
        return load_playlist(get_playlist_url(request.url))
    except ValueError as error:
        raise HTTPException(400, str(error))
    except ImportError:
        raise HTTPException(500, 'Install yt-dlp first: python -m pip install yt-dlp')
    except Exception:
        raise HTTPException(502, 'Could not load the playlist. Check your connection and playlist access.')


@app.post('/api/transcripts')
def transcripts(request: TranscriptRequest):
    lectures = [lecture.model_dump() for lecture in request.lectures]

    def job(progress):
        result = fetch_transcripts(lectures, progress=progress)
        forget_indexes(lecture['video_id'] for lecture in lectures)
        result['transcripts'] = [summarize_transcript(t) for t in result['transcripts']]
        return result

    return ndjson(run_with_progress(job))


@app.post('/api/local-folder')
def local_folder(request: FolderRequest):
    """List the audio/video files in a folder on this computer as lectures (same shape as /api/playlist)."""
    try:
        return scan_folder(request.path, recursive=request.recursive)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post('/api/transcribe')
def transcribe(request: TranscriptRequest):
    """Transcribe local files and caption-less YouTube lectures with Whisper (streams progress)."""
    lectures = [lecture.model_dump() for lecture in request.lectures]

    def job(progress):
        result = transcribe_lectures(lectures, progress=progress)
        forget_indexes(lecture['video_id'] for lecture in lectures)
        result['transcripts'] = [summarize_transcript(t) for t in result['transcripts']]
        return result

    return ndjson(run_with_progress(job))


MEDIA_TYPES = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
               '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.mkv': 'video/x-matroska',
               '.webm': 'video/webm', '.mov': 'video/quicktime'}


@app.get('/api/media/{lecture_id}')
def media(lecture_id: str):
    """Stream a local lecture's file to the in-app player. Supports HTTP Range requests for seeking.

    Only files registered by scanning a folder can be served, and only while they are unchanged.
    """
    if not is_local_id(lecture_id):
        raise HTTPException(404, 'Unknown local lecture.')
    try:
        path = lecture_media_path(lecture_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), 'application/octet-stream'),
                        headers={'Cache-Control': 'no-store'})


@app.post('/api/index')
def build_index(request: IndexRequest):
    def job(progress):
        index = get_index(request.video_ids, progress)
        return {'chunks': len(index.chunks), 'lectures': len(index.transcripts)}

    return ndjson(run_with_progress(job))


@app.post('/api/chat')
def chat(request: ChatRequest):
    def events():
        if not rag_pipeline.llm_available():
            raise ValueError('Add OPENROUTER_API_KEY to the .env file in the project folder, then restart the server.')
        yield {'type': 'status', 'message': 'Preparing lecture search...'}
        index = get_index(request.video_ids)
        yield {'type': 'status', 'message': 'Searching the lectures...'}
        history = [turn.model_dump() for turn in request.history]
        study = {'mode': request.mode, 'concept': request.concept.strip()}
        messages, sources, search = rag_pipeline.prepare_answer(index, request.question, study, history)
        yield {'type': 'sources', 'sources': [public_source(s) for s in sources], 'query': search['query']}
        yield {'type': 'status', 'message': 'Writing the answer...'}
        thinking_reported = False
        for kind, text in rag_pipeline.stream_llm(messages):
            if kind == 'token':
                yield {'type': 'token', 'text': text}
            elif not thinking_reported:
                thinking_reported = True
                yield {'type': 'status', 'message': 'Thinking...'}
        yield {'type': 'done'}

    return ndjson(events())


# Serve the built React app (npm run build) from the same server, if it exists.
FRONTEND_DIST = Path(__file__).resolve().parent / 'frontend' / 'dist'
if FRONTEND_DIST.is_dir():
    app.mount('/assets', StaticFiles(directory=FRONTEND_DIST / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        file = (FRONTEND_DIST / path).resolve()
        if path and file.is_file() and FRONTEND_DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / 'index.html')
else:
    @app.get('/', include_in_schema=False)
    def no_build():
        return {'message': 'The UI is not built yet. Run "npm run build" inside the frontend folder, '
                           'then restart this server. (Or use "npm run dev" and open http://localhost:5173.)'}


if __name__ == '__main__':
    # `python server.py` works from any folder, unlike `uvicorn server:app`.
    import os
    import uvicorn
    os.chdir(Path(__file__).resolve().parent)
    print('\n  Lecture Drift is running at  http://localhost:8000  (press Ctrl+C to stop)\n')
    uvicorn.run(app, host='127.0.0.1', port=8000)
