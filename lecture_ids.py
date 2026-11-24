"""Lecture identity shared by every source: YouTube videos and files on this computer.

A lecture id is either an 11-character YouTube video id, or "local_" + 12 hex characters derived
from a local file's path, size and modification time (so an edited file gets a new id).

In API requests and the frontend a lecture's origin is `source` ("youtube" | "local"). Saved transcript
files already use `source` for where the text came from ("youtube_captions" | "whisper"), so they store
the origin as `lecture_source`; a missing `lecture_source` means "youtube" (all older transcripts).
"""

import hashlib
import re
from pathlib import Path

YOUTUBE_ID_PATTERN = r'[A-Za-z0-9_-]{11}'
LOCAL_ID_PATTERN = r'local_[0-9a-f]{12}'
LECTURE_ID_PATTERN = rf'^(?:{YOUTUBE_ID_PATTERN}|{LOCAL_ID_PATTERN})$'
SOURCES = ('youtube', 'local')


def is_youtube_id(lecture_id):
    return bool(re.fullmatch(YOUTUBE_ID_PATTERN, lecture_id or ''))


def is_local_id(lecture_id):
    return bool(re.fullmatch(LOCAL_ID_PATTERN, lecture_id or ''))


def is_lecture_id(lecture_id):
    return is_youtube_id(lecture_id) or is_local_id(lecture_id)


def source_of(lecture_id):
    """Return 'youtube' or 'local' for a valid id, otherwise raise a plain error."""
    if is_local_id(lecture_id):
        return 'local'
    if is_youtube_id(lecture_id):
        return 'youtube'
    raise ValueError(f'"{lecture_id}" is not a valid lecture id.')


def local_lecture_id(path):
    """Stable id for a local media file: the same file keeps its id until it is moved or edited."""
    file = Path(path).resolve()
    info = file.stat()
    identity = f'{str(file).lower()}|{info.st_size}|{info.st_mtime_ns}'
    return 'local_' + hashlib.sha1(identity.encode('utf-8')).hexdigest()[:12]


def citation_link(lecture, seconds):
    """Link that opens a lecture at a moment.

    YouTube keeps the normal watch link with &t=. Local files get an app link to the media endpoint
    (served in a later phase); the frontend plays it in its own player at the #t= time.
    """
    seconds = int(seconds)
    if lecture.get('lecture_source', 'youtube') == 'local':
        return f'/api/media/{lecture["video_id"]}#t={seconds}'
    return f'{lecture["url"]}&t={seconds}s'
