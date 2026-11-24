"""Answer a student's question using only the lectures they chose, with timestamped citations.

Pipeline:
  1. Chunk each transcript into ~60 second windows that keep their timestamps.
  2. Embed chunks with a multilingual model (captions are Hinglish written in Devanagari,
     questions are usually English) and cache the vectors on disk per lecture.
  3. Rewrite the question with the LLM into a standalone English query plus a Devanagari
     Hinglish version, so both dense and keyword search can match the captions.
  4. Hybrid retrieval (dense + BM25, fused with reciprocal rank fusion) restricted to the
     chosen lectures. In comparison mode every lecture is guaranteed some passages.
  5. Generate a grounded answer with DeepSeek-V4-Flash (via OpenRouter) that cites passages
     as [L<no> mm:ss].
"""

import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

from lecture_ids import citation_link

PROJECT_FOLDER = Path(__file__).resolve().parent
INDEX_FOLDER = PROJECT_FOLDER / 'data' / 'rag_index'

EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'intfloat/multilingual-e5-base')
CHUNK_SECONDS = 60      # target length of one passage
OVERLAP_SECONDS = 15    # context carried into the next passage
MAX_CHUNK_CHARS = 1500  # keeps passages inside the embedding model's 512 token limit

# LLM access: OpenRouter by default (OPENROUTER_API_KEY); a direct DeepSeek key also works.
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODEL = 'deepseek/deepseek-v4-flash'
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
DEEPSEEK_MODEL = 'deepseek-v4-flash'


# ---------------------------------------------------------------- chunking

def format_time(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes:02d}:{secs:02d}'


def chunk_transcript(transcript):
    """Group caption segments into overlapping time windows."""
    segments = transcript['segments']
    chunks = []
    first = 0
    while first < len(segments):
        last = first
        chars = len(segments[first]['text'])
        while (last + 1 < len(segments)
               and segments[last]['end'] - segments[first]['start'] < CHUNK_SECONDS
               and chars + len(segments[last + 1]['text']) <= MAX_CHUNK_CHARS):
            last += 1
            chars += len(segments[last]['text']) + 1
        chunks.append({
            'chunk_id': f'{transcript["video_id"]}:{len(chunks)}',
            'video_id': transcript['video_id'],
            'title': transcript['title'],
            'position': transcript['position'],
            'url': transcript['url'],
            'first_segment': first,
            'last_segment': last,
            'start': segments[first]['start'],
            'end': segments[last]['end'],
            'text': ' '.join(segment['text'] for segment in segments[first:last + 1]),
        })
        if last + 1 >= len(segments):
            break
        # Start the next window OVERLAP_SECONDS before this one ends, but always move forward.
        next_first = last + 1
        while next_first - 1 > first and segments[last]['end'] - segments[next_first - 1]['start'] < OVERLAP_SECONDS:
            next_first -= 1
        first = next_first
    return chunks


# ---------------------------------------------------------------- embeddings + cache

_model = None


def get_embedding_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _uses_e5_prefixes():
    return 'e5' in EMBEDDING_MODEL.lower()


def embed_passages(texts):
    prefix = 'passage: ' if _uses_e5_prefixes() else ''
    return get_embedding_model().encode([prefix + text for text in texts], batch_size=16,
                                        normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)


def embed_queries(texts):
    prefix = 'query: ' if _uses_e5_prefixes() else ''
    return get_embedding_model().encode([prefix + text for text in texts],
                                        normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)


def _fingerprint(transcript):
    settings = f'v2-title|{EMBEDDING_MODEL}|{CHUNK_SECONDS}|{OVERLAP_SECONDS}|{MAX_CHUNK_CHARS}|{transcript["title"]}|'
    return hashlib.sha1((settings + transcript['text']).encode('utf-8')).hexdigest()


def index_lecture(transcript):
    """Return (chunks, embeddings) for one lecture, reusing the disk cache when valid."""
    model_folder = INDEX_FOLDER / re.sub(r'[^A-Za-z0-9_.-]', '_', EMBEDDING_MODEL)
    chunks_path = model_folder / (transcript['video_id'] + '.json')
    vectors_path = model_folder / (transcript['video_id'] + '.npy')
    fingerprint = _fingerprint(transcript)

    if chunks_path.is_file() and vectors_path.is_file():
        try:
            saved = json.loads(chunks_path.read_text(encoding='utf-8'))
            vectors = np.load(vectors_path)
            if saved.get('fingerprint') == fingerprint and len(saved['chunks']) == len(vectors):
                chunks = saved['chunks']
                # Titles and positions can change if the playlist is reordered; keep them current.
                for chunk in chunks:
                    chunk.update(title=transcript['title'], position=transcript['position'], url=transcript['url'])
                return chunks, vectors
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass

    chunks = chunk_transcript(transcript)
    # The English title gives Devanagari captions an English anchor, so English questions are not
    # pulled toward the one lecture whose captions happen to be in English script.
    vectors = embed_passages([f'{chunk["title"]}\n{chunk["text"]}' for chunk in chunks])
    model_folder.mkdir(parents=True, exist_ok=True)
    np.save(vectors_path, vectors)
    chunks_path.write_text(json.dumps({'fingerprint': fingerprint, 'chunks': chunks}, ensure_ascii=False),
                           encoding='utf-8')
    return chunks, vectors


# ---------------------------------------------------------------- keyword search

def tokenize(text):
    # ऀ-ॿ keeps Devanagari vowel signs attached to their letters.
    return re.findall(r'[0-9a-zऀ-ॿ]+', text.lower())


class BM25:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(doc)) for doc in documents]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0
        frequency = Counter(term for doc in self.docs for term in doc)
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in frequency.items()}

    def scores(self, query):
        terms = set(tokenize(query))
        result = np.zeros(len(self.docs), dtype=np.float32)
        for index, (doc, length) in enumerate(zip(self.docs, self.lengths)):
            score = 0.0
            for term in terms:
                count = doc.get(term)
                if count:
                    norm = self.k1 * (1 - self.b + self.b * length / self.average_length)
                    score += self.idf[term] * count * (self.k1 + 1) / (count + norm)
            result[index] = score
        return result


# ---------------------------------------------------------------- retrieval

class LectureIndex:
    """Searchable index over the lectures in one study request."""

    def __init__(self, transcripts, progress=print):
        self.transcripts = {t['video_id']: t for t in transcripts}
        self.chunks = []
        vector_blocks = []
        for number, transcript in enumerate(transcripts, start=1):
            progress(f'{number}/{len(transcripts)}: indexing {transcript["title"]}')
            chunks, vectors = index_lecture(transcript)
            self.chunks.extend(chunks)
            vector_blocks.append(vectors)
        if not self.chunks:
            raise ValueError('No transcript passages to search.')
        self.vectors = np.vstack(vector_blocks)
        self.bm25 = BM25([chunk['text'] for chunk in self.chunks])

    def search(self, queries, keyword_queries=(), top_k=8, per_lecture=0, rrf_k=60):
        """Hybrid search. Returns chunks with a fused score, best first.

        queries: texts for dense search (e.g. English + Devanagari rewrite).
        keyword_queries: texts for BM25 (Devanagari rewrite and keywords work best here).
        per_lecture: in comparison mode, also take the best N passages of every lecture.
        """
        rankings = []
        dense_scores = self.vectors @ embed_queries(list(queries)).T  # (chunks, queries)
        for column in range(dense_scores.shape[1]):
            rankings.append(np.argsort(-dense_scores[:, column]))
        for query in keyword_queries:
            scores = self.bm25.scores(query)
            if scores.max() > 0:
                ranked = np.argsort(-scores)
                rankings.append(ranked[scores[ranked] > 0])

        fused = np.zeros(len(self.chunks), dtype=np.float64)
        for ranking in rankings:
            fused[ranking] += 1.0 / (rrf_k + np.arange(1, len(ranking) + 1))
        best_dense = dense_scores.max(axis=1)

        chosen = list(np.argsort(-fused)[:top_k])
        if per_lecture:
            for video_id in self.transcripts:
                own = [i for i in np.argsort(-fused) if self.chunks[i]['video_id'] == video_id][:per_lecture]
                chosen.extend(i for i in own if i not in chosen)

        return [dict(self.chunks[i], score=float(fused[i]), similarity=float(best_dense[i])) for i in chosen]

    def build_context(self, hits):
        """Merge overlapping hits from the same lecture and order them by lecture and time."""
        hits = sorted(hits, key=lambda hit: (hit['position'], hit['start']))
        merged = []
        for hit in hits:
            previous = merged[-1] if merged else None
            if previous and previous['video_id'] == hit['video_id'] and hit['first_segment'] <= previous['last_segment'] + 1:
                previous['last_segment'] = max(previous['last_segment'], hit['last_segment'])
                previous['similarity'] = max(previous['similarity'], hit['similarity'])
            else:
                merged.append(dict(hit))
        for passage in merged:
            segments = self.transcripts[passage['video_id']]['segments']
            span = segments[passage['first_segment']:passage['last_segment'] + 1]
            passage['start'], passage['end'] = span[0]['start'], span[-1]['end']
            passage['text'] = ' '.join(segment['text'] for segment in span)
            passage['label'] = f'L{passage["position"]} {format_time(passage["start"])}'
            lecture = self.transcripts[passage['video_id']]
            passage['source'] = lecture.get('lecture_source', 'youtube')
            passage['link'] = citation_link(lecture, passage['start'])
        return merged


# ---------------------------------------------------------------- LLM (OpenAI-compatible chat API)

def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_FOLDER / '.env')
    except ImportError:
        pass


def llm_settings():
    """Return (url, api_key, model). LLM_MODEL overrides the default model for either provider."""
    _load_env()
    if os.environ.get('OPENROUTER_API_KEY'):
        return OPENROUTER_URL, os.environ['OPENROUTER_API_KEY'], os.environ.get('LLM_MODEL', OPENROUTER_MODEL)
    if os.environ.get('DEEPSEEK_API_KEY'):
        return DEEPSEEK_URL, os.environ['DEEPSEEK_API_KEY'], os.environ.get('LLM_MODEL', DEEPSEEK_MODEL)
    return None, None, None


def llm_available():
    return llm_settings()[1] is not None


def call_llm(messages, temperature=0.2, max_tokens=1500, json_mode=False):
    import requests
    url, api_key, model = llm_settings()
    if not api_key:
        raise ValueError('Set OPENROUTER_API_KEY in your environment or in a .env file to generate answers.')
    body = {'model': model, 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    response = requests.post(url, json=body, timeout=120, headers={'Authorization': 'Bearer ' + api_key})
    if response.status_code != 200:
        raise ValueError(f'LLM request to {model} failed ({response.status_code}): {response.text[:300]}')
    message = response.json()['choices'][0]['message']
    return message.get('content') or ''


def stream_llm(messages, temperature=0.2, max_tokens=2500):
    """Yield ('reasoning' | 'token', text) pieces as the model generates them (server-sent events)."""
    import requests
    url, api_key, model = llm_settings()
    if not api_key:
        raise ValueError('Set OPENROUTER_API_KEY in your environment or in a .env file to generate answers.')
    body = {'model': model, 'messages': messages, 'temperature': temperature,
            'max_tokens': max_tokens, 'stream': True}
    with requests.post(url, json=body, timeout=(15, 180), stream=True,
                       headers={'Authorization': 'Bearer ' + api_key}) as response:
        if response.status_code != 200:
            raise ValueError(f'LLM request to {model} failed ({response.status_code}): {response.text[:300]}')
        response.encoding = 'utf-8'
        for line in response.iter_lines(decode_unicode=True):
            # Blank lines separate events; lines starting with ':' are keep-alive comments.
            if not line or not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            event = json.loads(data)
            if event.get('error'):
                raise ValueError(f'LLM error: {event["error"].get("message", event["error"])}')
            choices = event.get('choices') or []
            if not choices:
                continue
            delta = choices[0].get('delta') or {}
            reasoning = delta.get('reasoning') or delta.get('reasoning_content')
            if reasoning:
                yield 'reasoning', reasoning
            if delta.get('content'):
                yield 'token', delta['content']


REWRITE_PROMPT = """You prepare search queries over lecture transcripts.
The transcripts are auto-generated (YouTube captions or Whisper speech recognition) from English or
Hindi-English (Hinglish) lectures. Hinglish may be written in Devanagari script, with English technical
terms transliterated (for example "रैग इवैल्स" = "RAG evals", "रिट्रीवर" = "retriever"), or in Roman
script ("retriever ka context precision kaise nikalte hain").

Given the student's concept, recent chat, and new question, return JSON with:
  "query": a standalone English search query capturing what the student needs,
  "hinglish": the same query as a lecturer would say it in Hinglish, in Devanagari script,
  "roman": the same Hinglish query written in Roman (Latin) script,
  "keywords": 3-8 key technical terms, each given twice: in Devanagari transliteration and in English.
Return only the JSON object."""


def rewrite_query(question, concept='', history=()):
    """Turn a follow-up question into search queries in both scripts. Falls back to the raw question."""
    fallback = {'query': question, 'hinglish': '', 'roman': '', 'keywords': []}
    if not llm_available():
        return fallback
    recent = '\n'.join(f'{turn["role"]}: {turn["content"][:400]}' for turn in list(history)[-4:])
    user = f'Concept the student struggles with: {concept or "(none given)"}\nRecent chat:\n{recent or "(none)"}\nQuestion: {question}'
    try:
        reply = call_llm([{'role': 'system', 'content': REWRITE_PROMPT}, {'role': 'user', 'content': user}],
                         temperature=0, max_tokens=800, json_mode=True)
        data = json.loads(re.search(r'\{.*\}', reply, re.S).group(0))
        keywords = data.get('keywords') or []
        return {'query': str(data.get('query') or question), 'hinglish': str(data.get('hinglish') or ''),
                'roman': str(data.get('roman') or ''),
                'keywords': [str(word) for word in keywords if str(word).strip()] if isinstance(keywords, list) else []}
    except Exception:
        return fallback


ANSWER_PROMPT = """You are a study assistant for a student learning from a YouTube lecture playlist.
Answer ONLY from the transcript passages provided. The passages are auto-generated (YouTube captions or
Whisper speech recognition) in English or Hinglish, written in Devanagari or Roman script; they contain
recognition errors, so read them for meaning.

Rules:
- Cite every claim with the passage label in square brackets, exactly as given, e.g. [L12 05:31].
- If the passages do not cover the question, say so plainly and suggest what to look for instead. Do not
  fill gaps with outside knowledge unless you clearly mark it as "Outside the lectures:".
- Reply in the language the student used (English by default), not in Devanagari. Never quote the
  Devanagari caption text; paraphrase what the lecturer said in the student's language instead.
- Explain clearly for a student: start with the core idea, then details and any examples the lecturer used.
{mode_rules}"""

COMPARISON_RULES = """- The student is comparing lectures. After the explanation, add a short section
  "Across the lectures" saying how each lecture treats the concept: introduces it, refines/extends it,
  or explains it differently. Note any apparent conflict, with citations from both sides."""

REFERENCE_RULES = """- The student uses the whole playlist as a reference. Point them to the best moments to rewatch."""


def prepare_answer(index, question, study_request, history=(), top_k=8):
    """Retrieval half of the pipeline. Returns (llm_messages, sources, search)."""
    comparison = study_request.get('mode') == 'comparison'
    search = rewrite_query(question, study_request.get('concept', ''), history)
    # Devanagari and Roman versions let both search methods match captions and Whisper text alike.
    dense_queries = [search['query']] + [q for q in (search['hinglish'], search['roman']) if q]
    if search['query'] != question:
        dense_queries.append(question)
    keyword_queries = [q for q in [search['hinglish'], search['roman'], ' '.join(search['keywords']), question] if q]

    hits = index.search(dense_queries, keyword_queries, top_k=top_k, per_lecture=2 if comparison else 0)
    sources = index.build_context(hits)

    context = '\n\n'.join(f'[{source["label"]}] (Lecture {source["position"]}: {source["title"]})\n{source["text"]}'
                          for source in sources)
    system = ANSWER_PROMPT.format(mode_rules=COMPARISON_RULES if comparison else REFERENCE_RULES)
    messages = [{'role': 'system', 'content': system}]
    for turn in list(history)[-6:]:
        messages.append({'role': turn['role'], 'content': turn['content']})
    concept = study_request.get('concept')
    messages.append({'role': 'user', 'content':
                     (f'Concept I am struggling with: {concept}\n\n' if concept else '')
                     + f'Transcript passages:\n\n{context}\n\nQuestion: {question}'})
    return messages, sources, search


def answer_question(index, question, study_request, history=(), top_k=8):
    """Run the full RAG pipeline without streaming. Returns {'answer', 'sources', 'search'}."""
    messages, sources, search = prepare_answer(index, question, study_request, history, top_k)
    answer = call_llm(messages, max_tokens=2500)
    return {'answer': link_citations(answer, sources), 'sources': sources, 'search': search}


def link_citations(answer, sources):
    """Turn [L12 05:31] citations into markdown links that open YouTube at that moment."""
    links = {source['label']: source['link'] for source in sources}

    def replace(match):
        label = match.group(1)
        return f'[{label}]({links[label]})' if label in links else match.group(0)

    return re.sub(r'\[(L\d+ (?:\d+:)?\d{2}:\d{2})\](?!\()', replace, answer)


# ---------------------------------------------------------------- command line check

def load_saved_transcripts(video_ids=None):
    """All saved transcripts (YouTube captions, then Whisper), one per lecture id."""
    found = {}
    for folder in ('youtube_transcripts', 'transcripts'):
        for path in sorted((PROJECT_FOLDER / 'data' / folder).glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            if (isinstance(data, dict) and data.get('segments') and data.get('video_id') not in found
                    and (not video_ids or data.get('video_id') in video_ids)):
                found[data['video_id']] = data
    return sorted(found.values(), key=lambda t: t.get('position', 0))


if __name__ == '__main__':
    import argparse
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Ask a question over saved lecture transcripts.')
    parser.add_argument('question')
    parser.add_argument('--videos', nargs='*', help='video IDs to search (default: all saved)')
    parser.add_argument('--compare', action='store_true', help='comparison mode')
    parser.add_argument('--retrieve-only', action='store_true', help='skip the LLM answer')
    args = parser.parse_args()

    lecture_index = LectureIndex(load_saved_transcripts(args.videos))
    request = {'mode': 'comparison' if args.compare else 'reference', 'concept': ''}
    if args.retrieve_only:
        found = lecture_index.search([args.question], [args.question],
                                     per_lecture=2 if args.compare else 0)
        for passage in lecture_index.build_context(found):
            print(f'[{passage["label"]}] sim={passage["similarity"]:.3f} {passage["title"][:50]}\n  {passage["text"][:200]}\n')
    else:
        result = answer_question(lecture_index, args.question, request)
        print(result['answer'])
