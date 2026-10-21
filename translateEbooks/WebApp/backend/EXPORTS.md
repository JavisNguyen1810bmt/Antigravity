# Lesson slides and summary video

The existing translation WebSocket remains unchanged. When it has written
`translated_document.tex`, the web app can call the export API for the same
guest session.

## Architecture

```text
frontend/src/components/ExportTools.jsx
  ├─ POST /api/exports/{session}/slides -> Gemini -> lesson_slides.json
  │    ├─ GET .../slides.pptx -> python-pptx
  │    └─ GET .../slides.pdf  -> ReportLab
  └─ POST /api/exports/{session}/video -> in-process background job
       ├─ Gemini -> scene script JSON
       ├─ Edge TTS -> Vietnamese scene audio
       ├─ Pillow -> captioned visual cards
       └─ FFmpeg -> summary_video.mp4 + summary_video.srt
```

Generated files live under each guest session's `exports/` folder. The slide
deck is persisted as JSON, so a refreshed page can reload its preview without
calling the LLM again. Video jobs are kept in process memory; use a persistent
queue and shared job store (for example Redis + RQ/Celery) when deploying more
than one worker or requiring jobs to survive restarts.

## Dependencies

Install Python packages in the backend virtual environment:

```sh
python -m pip install -r requirements.txt
```

Install the system packages listed in `system-requirements.txt`, including
FFmpeg/FFprobe and DejaVu Sans. Configure `GEMINI_API_KEY` or comma/newline
separated `GEMINI_API_KEYS`. `GEMINI_EXPORT_MODEL` defaults to
`gemini-3.8-flash`; on overload, requests retry with exponential backoff
(3, 6, and 12 seconds plus jitter) before switching to the comma-separated
models in `GEMINI_EXPORT_FALLBACK_MODELS` (defaults to
`gemini-3.6-flash,gemini-2.5-flash-lite`). Set the fallback list to models
enabled for your Google AI account. Gemini calls time out after 45 seconds and
video jobs expose an explicit retryable overload state when all available
models are exhausted. Edge TTS uses Microsoft's online Vietnamese voice
`vi-VN-HoaiMyNeural`, so video generation needs outbound network access.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/exports/{session_id}/slides` | Create and return the lesson outline |
| `GET` | `/api/exports/{session_id}/slides` | Load a saved preview |
| `GET` | `/api/exports/{session_id}/slides.pptx` | Download PowerPoint |
| `GET` | `/api/exports/{session_id}/slides.pdf` | Download slide PDF |
| `POST` | `/api/exports/{session_id}/video` | Start video job (`202 Accepted`) |
| `GET` | `/api/exports/{session_id}/video/{job_id}` | Poll status/progress |
| `GET` | `/api/exports/{session_id}/video/{job_id}.mp4` | Download finished video |
| `GET` | `/api/exports/{session_id}/video/{job_id}.srt` | Download subtitles |

Each API checks that the guest session and translated document still exist.
The MP4 contains captioned scene cards generated locally from the script's
titles, keywords and short captions. The optional SRT contains the full
narration for external players/editors.

The translated document is sent to Gemini when creating slides/scripts, and
the generated narration is sent to the Edge TTS service. Configure provider
and data-handling policy to match your deployment's privacy requirements.
