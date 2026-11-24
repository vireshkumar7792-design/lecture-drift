"""Use a folder of lecture recordings on this computer as a lecture source.

Scanning a folder lists its audio/video files as lectures (it never reads file contents) and remembers
which lecture id belongs to which file in data/local_lectures.json. Later steps (Whisper transcription,
the media player) look files up by id there, so the browser never sends file paths for them.
"""

import json
import re
import threading
from pathlib import Path

from lecture_ids import local_lecture_id

PROJECT_FOLDER = Path(__file__).resolve().parent
REGISTRY_PATH = PROJECT_FOLDER / 'data' / 'local_lectures.json'

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov'}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

_registry_lock = threading.Lock()


def natural_key(text):
    """Sort "Lecture 2" before "Lecture 10" (plain sorting would put 10 first)."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', text)]


def media_kind(path):
    return 'video' if Path(path).suffix.lower() in VIDEO_EXTENSIONS else 'audio'


def resolve_folder(user_path):
    """Turn a typed or pasted folder path into an existing directory, or raise a plain error."""
    text = (user_path or '').strip().strip('"').strip("'").strip()
    if not text:
        raise ValueError('Type the path of a folder, for example C:\\Users\\you\\Videos\\My Course.')
    folder = Path(text).expanduser()
    try:
        folder = folder.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ValueError(f'The folder "{text}" does not exist. Check the path and try again.') from None
    if not folder.is_dir():
        raise ValueError(f'"{text}" is a file, not a folder. Enter the folder that contains your lectures.')
    return folder


def scan_folder(user_path, recursive=False):
    """List a folder's audio/video files as lectures, in natural filename order.

    Returns the same shape as playlist.load_playlist: {title, url, lectures, skipped}.
    """
    folder = resolve_folder(user_path)
    try:
        entries = [path for path in (folder.rglob('*') if recursive else folder.iterdir()) if path.is_file()]
    except PermissionError:
        raise ValueError(f'Cannot read the folder "{folder}" (permission denied).') from None

    media = [path for path in entries if path.suffix.lower() in MEDIA_EXTENSIONS]
    if not media:
        allowed = ', '.join(sorted(extension[1:] for extension in MEDIA_EXTENSIONS))
        raise ValueError(f'No audio or video files were found in "{folder}". Supported types: {allowed}.')
    media.sort(key=lambda path: natural_key(str(path.relative_to(folder))))

    lectures, registry = [], {}
    for number, path in enumerate(media, start=1):
        lecture_id = local_lecture_id(path)
        lectures.append({
            'position': number,
            'video_id': lecture_id,
            'title': path.stem,
            'source': 'local',
            'url': '',
            'file_name': path.name,
            'kind': media_kind(path),
            'size': path.stat().st_size,
        })
        registry[lecture_id] = {'path': str(path), 'title': path.stem, 'kind': media_kind(path)}
    _remember(registry)

    return {'title': folder.name or str(folder), 'url': str(folder), 'source': 'local',
            'lectures': lectures, 'skipped': len(entries) - len(media)}


def _read_registry():
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _remember(entries):
    with _registry_lock:
        registry = _read_registry()
        registry.update(entries)
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding='utf-8')


def lecture_media_path(lecture_id):
    """Return the file behind a local lecture id, checking it still exists and is unchanged."""
    with _registry_lock:
        entry = _read_registry().get(lecture_id)
    if not entry:
        raise ValueError('This local lecture is unknown. Load its folder again.')
    path = Path(entry['path'])
    if not path.is_file():
        raise ValueError(f'The file "{path.name}" was moved or deleted. Load its folder again.')
    if local_lecture_id(path) != lecture_id:
        raise ValueError(f'The file "{path.name}" has changed since its folder was loaded. Load the folder again.')
    return path
