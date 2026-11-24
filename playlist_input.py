"""Choose lectures and fetch their existing YouTube captions for later analysis."""

import json
import re
from urllib.parse import parse_qs, urlparse

import streamlit as st

from youtube_transcripts import fetch_transcripts


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
            'url': 'https://www.youtube.com/watch?v=' + video_id,
        })

    if not lectures:
        raise ValueError('No available lectures were found in this playlist.')

    return {'title': playlist.get('title') or 'YouTube playlist',
            'url': playlist_url, 'lectures': lectures, 'skipped': skipped}


def main():
    st.set_page_config(page_title='Lecture Drift Detector', page_icon='📚', layout='wide')
    st.title('Lecture Drift Detector')
    st.write('Choose a playlist and tell us how you want to study it.')

    user_url = st.text_input('YouTube playlist link', placeholder='https://www.youtube.com/playlist?list=...')

    # Changing the link clears choices belonging to the previous playlist.
    if user_url.strip() != st.session_state.get('loaded_url', ''):
        for key in ['playlist', 'study_request', 'lecture_selection', 'caption_result']:
            st.session_state.pop(key, None)

    if st.button('Load playlist', type='primary'):
        for key in ['playlist', 'study_request', 'lecture_selection', 'caption_result']:
            st.session_state.pop(key, None)
        try:
            playlist_url = get_playlist_url(user_url)
            with st.spinner('Reading the playlist titles...'):
                st.session_state['playlist'] = load_playlist(playlist_url)
            st.session_state['loaded_url'] = user_url.strip()
        except ImportError:
            st.error('Install yt-dlp first: python -m pip install yt-dlp')
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error('Could not load the playlist. Check your connection and playlist access, then retry.')

    playlist = st.session_state.get('playlist')
    if not playlist:
        return

    lectures = playlist['lectures']
    st.subheader(playlist['title'])
    st.caption(f'{len(lectures)} lectures available. Lecture content has not been analyzed yet.')
    if playlist['skipped']:
        st.warning(f'{playlist["skipped"]} unavailable playlist entries were skipped.')
    st.dataframe([{'No.': lecture['position'], 'Lecture': lecture['title'], 'YouTube link': lecture['url']}
                  for lecture in lectures], hide_index=True, width='stretch')

    mode = st.radio('How would you like to use this playlist?',
                    ['Compare selected lectures', 'Study the whole playlist as a reference'])

    if mode == 'Compare selected lectures':
        selected = st.multiselect(
            'Select at least two lectures to compare', options=list(range(len(lectures))),
            format_func=lambda index: f'{lectures[index]["position"]}. {lectures[index]["title"]}',
            key='lecture_selection',
        )
        # Keep teaching order even if the student selects lectures in a different order.
        chosen_lectures = [lecture for index, lecture in enumerate(lectures) if index in selected]
        study_mode = 'comparison'
    else:
        chosen_lectures = lectures
        study_mode = 'reference'
        st.info('All available lectures will be used as study references. Explanations will not be compared.')

    concept = st.text_area('Which concept are you struggling with? (optional)',
                           placeholder='For example: I understand derivatives, but gradient descent is confusing.',
                           help='Leave this blank if you want to study the playlist in general.')

    study_request = {
        'playlist_title': playlist['title'],
        'playlist_url': playlist['url'],
        'mode': study_mode,
        'concept': concept.strip(),
        'lectures': chosen_lectures,
    }

    # Editing the mode, lectures, or concept invalidates an older saved choice.
    if st.session_state.get('study_request') != study_request:
        st.session_state.pop('study_request', None)
        st.session_state.pop('caption_result', None)

    if st.button('Save study choices', type='primary'):
        if study_mode == 'comparison' and len(chosen_lectures) < 2:
            st.error('Please select at least two lectures for comparison.')
        else:
            # The next project step can read this dictionary to process the lectures.
            st.session_state['study_request'] = study_request

    if 'study_request' in st.session_state:
        st.success('Your study choices are saved. You can now fetch YouTube transcripts.')
        st.write('**Mode:**', mode)
        st.write('**Concept:**', concept.strip() or 'General study')
        st.write('**Lectures:**', len(chosen_lectures))
        for lecture in chosen_lectures:
            st.write(f'{lecture["position"]}. {lecture["title"]}')

        st.subheader('Get YouTube transcripts')
        st.caption('Fetch existing manual or auto-generated YouTube captions. '
                   'Saved transcripts are reused. Videos without captions are deferred to a later stage.')
        if st.button('Fetch YouTube transcripts', type='primary'):
            try:
                with st.status('Fetching captions...', expanded=True) as status:
                    result = fetch_transcripts(chosen_lectures, progress=st.write)
                    st.session_state['caption_result'] = result
                    if result['errors']:
                        status.update(label='Caption fetching finished with some errors', state='error')
                    else:
                        ready = len(result['transcripts'])
                        deferred_count = len(result['deferred'])
                        status.update(label=f'Caption check complete: {ready} ready, {deferred_count} deferred',
                                      state='complete', expanded=False)
            except Exception as error:
                st.error(str(error))

        result = st.session_state.get('caption_result')
        if result:
            if result['transcripts']:
                st.success(f'{len(result["transcripts"])} lecture transcripts are ready.')
                if study_mode == 'comparison' and len(result['transcripts']) < 2:
                    st.warning('Comparison needs at least two lectures with transcripts. '
                               'Select another lecture with captions to continue with comparison later.')
            elif not result['errors']:
                st.info('No transcripts are ready from this selection. The lectures without captions are deferred for later.')

            deferred_lectures = result.get('deferred', [])
            if deferred_lectures:
                st.info(f'{len(deferred_lectures)} lectures have no captions and will be handled in a later stage.')
                with st.expander('Deferred for later'):
                    for lecture in deferred_lectures:
                        st.write(f'{lecture["position"]}. {lecture["title"]}')
                        st.caption(lecture['reason'])
                    st.caption('This list is also saved in data/deferred_lectures.json.')
                    st.download_button('Download deferred lecture list',
                        data=json.dumps(deferred_lectures, indent=2, ensure_ascii=False),
                        file_name='deferred_lectures.json', mime='application/json')
            for error in result['errors']:
                st.error(f'{error["title"]}: {error["error"]}')
            for transcript in result['transcripts']:
                with st.expander(transcript['title']):
                    source = 'Auto-generated YouTube captions' if transcript['is_generated'] else 'Manual YouTube captions'
                    st.caption(f'{source} | Language: {transcript["language"]} | '
                               f'{len(transcript["segments"])} timestamped segments')
                    st.write(transcript['text'])
                    st.download_button('Download transcript JSON',
                        data=json.dumps(transcript, indent=2, ensure_ascii=False),
                        file_name=transcript['video_id'] + '.json', mime='application/json',
                        key='download_' + str(transcript['position']) + '_' + transcript['video_id'])

            if result['transcripts']:
                show_study_chat(st.session_state['study_request'], result['transcripts'])


@st.cache_resource(show_spinner=False)
def load_embedding_model():
    """Load the embedding model once per server process instead of on every rerun."""
    import rag_pipeline
    return rag_pipeline.get_embedding_model()


def show_study_chat(study_request, transcripts):
    """Ask questions about the chosen lectures, answered from their transcripts with citations."""
    import rag_pipeline

    st.subheader('Ask about your lectures')
    st.caption('Answers use only the transcripts of the lectures you chose. '
               'Citations such as [L3 05:31] open the lecture at that moment.')

    # Rebuild the index when the set of lectures with transcripts changes.
    index_key = tuple(sorted(transcript['video_id'] for transcript in transcripts))
    if st.session_state.get('rag_index_key') != index_key:
        st.session_state.pop('rag_index', None)
        st.session_state['chat_history'] = []
        st.session_state['rag_index_key'] = index_key

    if 'rag_index' not in st.session_state:
        try:
            with st.status('Preparing lecture search (first run downloads the model and may take a few minutes)...',
                           expanded=True) as status:
                load_embedding_model()
                st.session_state['rag_index'] = rag_pipeline.LectureIndex(transcripts, progress=st.write)
                status.update(label='Lecture search is ready', state='complete', expanded=False)
        except ImportError:
            st.error('Install search support: python -m pip install sentence-transformers')
            return
        except Exception as error:
            st.error(f'Could not prepare lecture search: {error}')
            return

    if not rag_pipeline.llm_available():
        st.warning('Add OPENROUTER_API_KEY to your environment or a .env file in the project folder to get answers.')
        return

    history = st.session_state['chat_history']
    for turn in history:
        with st.chat_message(turn['role']):
            st.markdown(turn['content'])
            show_sources(turn.get('sources'))

    question = None
    concept = study_request['concept']
    if not history and concept and st.button('Explain the concept I am struggling with', type='primary'):
        question = concept
    question = st.chat_input('Ask a question about these lectures') or question
    if not question:
        return

    with st.chat_message('user'):
        st.markdown(question)
    with st.chat_message('assistant'):
        try:
            with st.spinner('Searching the lectures and writing an answer...'):
                result = rag_pipeline.answer_question(
                    st.session_state['rag_index'], question, study_request,
                    history=[{'role': turn['role'], 'content': turn['content']} for turn in history])
        except Exception as error:
            st.error(str(error))
            return
        st.markdown(result['answer'])
        show_sources(result['sources'])
    history.append({'role': 'user', 'content': question})
    history.append({'role': 'assistant', 'content': result['answer'], 'sources': result['sources']})


def show_sources(sources):
    if not sources:
        return
    with st.expander(f'Transcript passages used ({len(sources)})'):
        for source in sources:
            st.markdown(f'**[{source["label"]}]({source["link"]})** · Lecture {source["position"]}: {source["title"]}')
            st.caption(source['text'][:600] + ('...' if len(source['text']) > 600 else ''))


if __name__ == '__main__':
    main()
