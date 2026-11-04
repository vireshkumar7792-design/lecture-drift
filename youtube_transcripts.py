"""Get existing YouTube captions and defer lectures without captions for later."""

import json
import re
from pathlib import Path


TRANSCRIPT_FOLDER = Path(__file__).resolve().parent / 'data' / 'youtube_transcripts'


def get_video_transcript(video_id):
    """Return timestamped captions, or None when this lecture has no captions."""
    from youtube_transcript_api import (YouTubeTranscriptApi, NoTranscriptFound,
                                        TranscriptsDisabled, VideoUnavailable, RequestBlocked)

    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
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
    deferred_path = TRANSCRIPT_FOLDER.parent / 'deferred_lectures.json'
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
        try:
            video_id = lecture['video_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
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
                deferred_lecture = {
                    'video_id': video_id,
                    'title': title,
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
