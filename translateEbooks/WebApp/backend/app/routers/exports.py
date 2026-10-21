import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from google.genai.errors import APIError
from pydantic import ValidationError

from app.config import GUEST_UPLOADS_DIR, guest_sessions
from app.services.content_exports import (
    GeminiOverloadedError,
    LessonDeck,
    MissingGeminiKeyError,
    create_pdf,
    create_pptx,
    generate_lesson_deck,
    load_deck,
    save_deck,
)
from app.services.video_export import create_summary_video

router = APIRouter(prefix="/api/exports", tags=["exports"])
logger = logging.getLogger(__name__)
video_jobs: dict[str, dict] = {}


def _session_paths(session_id: str) -> tuple[Path, Path]:
    if session_id not in guest_sessions:
        raise HTTPException(status_code=404, detail="Phiên dịch không tồn tại hoặc đã hết hạn.")

    root = Path(GUEST_UPLOADS_DIR).resolve()
    session_dir = (root / session_id).resolve()
    if root not in session_dir.parents:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên dịch.")

    translated_file = session_dir / "translated_document.tex"
    if not translated_file.is_file():
        raise HTTPException(status_code=409, detail="Chưa có bản dịch để tạo học liệu.")
    return session_dir, translated_file


def _read_translated_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Không thể đọc file bản dịch.") from exc


@router.post("/{session_id}/slides")
async def generate_slides(session_id: str):
    session_dir, translated_file = _session_paths(session_id)
    try:
        deck = await generate_lesson_deck(_read_translated_file(translated_file))
        save_deck(deck, session_dir / "lesson_slides.json")
    except MissingGeminiKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GeminiOverloadedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RuntimeError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=502, detail=f"Không thể tạo slide: {exc}") from exc
    except APIError as exc:
        try:
            upstream_status = int(exc.code)
        except (TypeError, ValueError):
            upstream_status = 502
        status_code = 503 if upstream_status in {429, 503} else 502
        detail = (
            "Gemini đang quá tải sau nhiều lần thử. Vui lòng chờ một chút rồi thử lại."
            if status_code == 503
            else f"Gemini không thể tạo slide: {exc}"
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return deck.model_dump()


@router.get("/{session_id}/slides")
async def get_slides(session_id: str):
    session_dir, _ = _session_paths(session_id)
    deck_path = session_dir / "lesson_slides.json"
    if not deck_path.is_file():
        raise HTTPException(status_code=404, detail="Chưa tạo bài giảng cho phiên dịch này.")
    try:
        return load_deck(deck_path).model_dump()
    except (OSError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=500, detail="Không thể đọc dữ liệu bài giảng.") from exc


@router.get("/{session_id}/slides.pptx")
async def download_slides_pptx(session_id: str):
    session_dir, _ = _session_paths(session_id)
    deck_path = session_dir / "lesson_slides.json"
    if not deck_path.is_file():
        raise HTTPException(status_code=404, detail="Hãy tạo bài giảng trước khi tải file.")
    deck = load_deck(deck_path)
    output_path = session_dir / "lesson_slides.pptx"
    try:
        create_pptx(deck, output_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Không thể xuất PowerPoint: {exc}") from exc
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="bai-giang.pptx",
    )


@router.get("/{session_id}/slides.pdf")
async def download_slides_pdf(session_id: str):
    session_dir, _ = _session_paths(session_id)
    deck_path = session_dir / "lesson_slides.json"
    if not deck_path.is_file():
        raise HTTPException(status_code=404, detail="Hãy tạo bài giảng trước khi tải file.")
    deck = load_deck(deck_path)
    output_path = session_dir / "lesson_slides.pdf"
    try:
        create_pdf(deck, output_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Không thể xuất PDF: {exc}") from exc
    return FileResponse(output_path, media_type="application/pdf", filename="bai-giang.pdf")


async def _render_video_job(session_id: str, job_id: str, source_path: Path, output_dir: Path) -> None:
    job = video_jobs[job_id]

    async def update_status(message: str, progress: int) -> None:
        job.update(status="processing", message=message, progress=progress)

    try:
        duration, video_path = await create_summary_video(
            _read_translated_file(source_path),
            output_dir,
            update_status,
        )
        job.update(
            status="completed",
            message="Video đã sẵn sàng.",
            progress=100,
            duration_seconds=round(duration, 1),
            download_url=f"/api/exports/{session_id}/video/{job_id}/download",
            subtitles_url=f"/api/exports/{session_id}/video/{job_id}.srt",
            file_size=video_path.stat().st_size,
        )
    except GeminiOverloadedError as exc:
        logger.warning("Summary video job %s could not reach Gemini: %s", job_id, exc)
        job.update(
            status="failed",
            error_code="AI_OVERLOADED",
            retryable=True,
            message=str(exc),
            progress=100,
        )
    except Exception as exc:
        logger.exception("Summary video job %s failed", job_id)
        job.update(
            status="failed",
            error_code="EXPORT_FAILED",
            retryable=False,
            message=str(exc),
            progress=100,
        )


@router.post("/{session_id}/video", status_code=202)
async def start_summary_video(session_id: str):
    session_dir, translated_file = _session_paths(session_id)
    job_id = uuid.uuid4().hex
    output_dir = session_dir / "exports" / f"video_{job_id}"
    video_jobs[job_id] = {
        "job_id": job_id,
        "session_id": session_id,
        "status": "queued",
        "message": "Đang chờ xử lý.",
        "progress": 0,
    }
    asyncio.create_task(_render_video_job(session_id, job_id, translated_file, output_dir))
    return video_jobs[job_id].copy()


def _completed_video_job(session_id: str, job_id: str) -> dict:
    _session_paths(session_id)
    job = video_jobs.get(job_id)
    if job is None or job["session_id"] != session_id or job["status"] != "completed":
        raise HTTPException(status_code=404, detail="Video chưa sẵn sàng.")
    return job


@router.get("/{session_id}/video/{job_id}.mp4")
@router.get("/{session_id}/video/{job_id}/download")
async def download_summary_video(session_id: str, job_id: str):
    _completed_video_job(session_id, job_id)
    session_dir, _ = _session_paths(session_id)
    video_path = session_dir / "exports" / f"video_{job_id}" / "summary_video.mp4"
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file video.")
    return FileResponse(video_path, media_type="video/mp4", filename="video-tom-tat.mp4")


@router.get("/{session_id}/video/{job_id}.srt")
async def download_summary_subtitles(session_id: str, job_id: str):
    _completed_video_job(session_id, job_id)
    session_dir, _ = _session_paths(session_id)
    subtitle_path = session_dir / "exports" / f"video_{job_id}" / "summary_video.srt"
    if not subtitle_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file phụ đề.")
    return FileResponse(subtitle_path, media_type="application/x-subrip", filename="video-tom-tat.srt")


@router.get("/{session_id}/video/{job_id}")
async def get_summary_video_status(session_id: str, job_id: str):
    _session_paths(session_id)
    job = video_jobs.get(job_id)
    if job is None or job["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình tạo video.")
    return job.copy()
