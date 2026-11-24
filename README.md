# Lecture Drift Detector

Study a lecture course with an AI tutor. Bring lectures from a **YouTube playlist**, a **folder of
recordings on your computer**, or both mixed together. Put them in your own teaching order, ask
questions, and get answers that stream in token by token, citing the exact lecture moments they come
from. Clicking a citation plays that moment: YouTube videos in an embedded player, local files in the
app's own audio/video player.

- **Backend:** FastAPI (`server.py`) around the RAG pipeline (`rag_pipeline.py`): multilingual embeddings
  + BM25 hybrid search over transcripts, answers from DeepSeek-V4-Flash via OpenRouter.
- **Transcripts:** YouTube captions when they exist (`youtube_transcripts.py`); otherwise Whisper on the
  CPU (`audio_transcripts.py`, faster-whisper). Both produce the same transcript format.
- **Frontend:** React + TypeScript + Tailwind CSS (Vite) in `frontend/`.

## Setup (once)

```
pip install -r requirements.txt
cd frontend
npm install
```

Create `.env` in the project folder:

```
OPENROUTER_API_KEY=sk-or-v1-...
# optional: LLM_MODEL=deepseek/deepseek-v4.1-flash
# optional Whisper settings, see below
```

## Run

Build the UI once (and again after changing frontend code), then start the server. Run each line
separately (Windows PowerShell does not support `&&`):

```
cd frontend
npm run build
cd ..
python server.py
```

Open http://localhost:8000 and keep that terminal open while you use the site.

**While editing the UI (hot reload):** run `python server.py` in one terminal and `cd frontend; npm run dev`
in another, then open http://localhost:5173.

## Lecture sources

1. **YouTube playlist:** paste a link containing `list=...`.
2. **Folder on this computer:** type or paste a folder path, e.g. `C:\Users\you\Videos\My Course`
   (in File Explorer: right-click the folder → *Copy as path*). Supported files: mp3, wav, m4a, aac, flac,
   ogg, mp4, mkv, webm, mov. Files are listed in natural filename order (Lecture 2 before Lecture 10);
   tick *Include subfolders* to scan nested folders. Each file gets a stable id from its path, size and
   modification time, so editing a file makes it a new lecture.
3. **Mix them:** on the lecture screen use **Add another source**, then drag lectures (or use ↑/↓) in the
   **Teaching order** list. That order decides the lecture numbers (L1, L2, …) used in answers and in
   comparison mode.

## Whisper (local files and YouTube videos without captions)

- YouTube lectures without captions: only the audio is downloaded (yt-dlp, into `data/audio/`) and then
  transcribed. If download or transcription fails, the lecture stays in `data/deferred_lectures.json`
  with the reason; one failure never stops the other lectures.
- Transcripts are saved in `data/transcripts/<lecture id>.json` and reused. A file is recognised by its
  content too, so a moved or renamed file is not transcribed again. Writes are atomic: an interrupted run
  is safe to repeat.
- Settings (in `.env` or the environment):

| Variable | Default | Notes |
|---|---|---|
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`. Bigger is more accurate and slower. |
| `WHISPER_LANGUAGE` | auto-detect | e.g. `hi` or `en` to skip detection. |
| `WHISPER_BEAM_SIZE` | `5` | `1` is noticeably faster on CPU with slightly lower accuracy. |

Model download sizes (once, cached by Hugging Face): tiny ≈ 75 MB, base ≈ 145 MB, small ≈ 460 MB,
medium ≈ 1.5 GB, large-v3 ≈ 3 GB. See "Expected speed" below for CPU timings measured on this project.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Whether an LLM key is configured, model names |
| `POST /api/playlist` | Read a YouTube playlist's titles and links |
| `POST /api/local-folder` | List the audio/video files of a folder as lectures (`{path, recursive}`) |
| `POST /api/transcripts` | Fetch/reuse YouTube captions (streams NDJSON progress) |
| `POST /api/transcribe` | Whisper for local files and caption-less YouTube lectures (streams per-lecture progress) |
| `GET /api/media/{id}` | Stream a local lecture file to the player, with HTTP Range support (registered files only) |
| `POST /api/index` | Build/reuse the search index (streams progress) |
| `POST /api/chat` | RAG answer: streams `status`, `sources`, then `token` events |

Command line check without the UI:

```
python rag_pipeline.py "What is benchmark contamination?" --retrieve-only
```
