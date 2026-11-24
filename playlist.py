"""Read a YouTube playlist's lecture titles and links (no video download)."""

import re
from urllib.parse import parse_qs, urlparse


def get_playlist_url(user_url):
    """Check the link and return a clean YouTube playlist URL."""
    user_url = user_url.strip()
    if '://' not in user_url:
        user_url = 'https://' + user_url

    link = urlparse(user_url)
    allowed_hosts = ['youtube.com', 'www.youtube.com', 'm.youtube.com',
                     'music.youtube.com', 'youtu.be', 'www.youtu.be']
    if link.scheme not in ['http', 'https'] or link.hostname not in allowed_hosts:
        raise ValueError('Please enter a YouTube playlist link.')

    playlist_id = parse_qs(link.query).get('list', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,100}', playlist_id):
        raise ValueError('This link has no valid playlist ID. Copy a link containing list=...')

    return 'https://www.youtube.com/playlist?list=' + playlist_id


def load_playlist(playlist_url):
    """Read lecture titles and links. This step does not download videos."""
    from yt_dlp import YoutubeDL

    options = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'socket_timeout': 20,
        'retries': 1,
    }
    with YoutubeDL(options) as youtube:
        playlist = youtube.extract_info(playlist_url, download=False)

    if not playlist:
        raise ValueError('Could not read the playlist. Check that it is accessible and try again.')

    lectures = []
    skipped = 0
    for number, video in enumerate(playlist.get('entries') or [], start=1):
        if not video:
            skipped += 1
            continue
        video_id = video.get('id', '')
        title = video.get('title') or 'Untitled lecture'
        if (not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id)
                or title in ['[Private video]', '[Deleted video]']
                or video.get('availability') in ['private', 'needs_auth', 'premium_only', 'subscriber_only']):
            skipped += 1
            continue
        lectures.append({
            'position': video.get('playlist_index') or number,
            'video_id': video_id,
            'title': title,
            'source': 'youtube',
            'url': 'https://www.youtube.com/watch?v=' + video_id,
        })

    if not lectures:
        raise ValueError('No available lectures were found in this playlist.')

    return {'title': playlist.get('title') or 'YouTube playlist',
            'url': playlist_url, 'lectures': lectures, 'skipped': skipped}
