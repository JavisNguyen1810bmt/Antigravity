import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pptx import Presentation

from app.config import GUEST_UPLOADS_DIR, guest_sessions
from app.main import app
from app.routers.exports import _render_video_job, video_jobs
from app.services.content_exports import (
    GeminiOverloadedError,
    LessonDeck,
    LessonSlide,
    _generate_json_with_status,
    create_pdf,
    create_pptx,
    translated_body,
)
from app.services.video_export import (
    VideoScene,
    _create_scene_image,
    _srt_time,
    create_summary_video,
)


class ContentExportTests(unittest.TestCase):
    def setUp(self):
        self.deck = LessonDeck(
            title="Nền tảng dữ liệu",
            objectives=["Hiểu khái niệm dữ liệu", "Biết cách vận dụng"],
            slides=[
                LessonSlide(
                    title="Khái niệm",
                    bullets=["Dữ liệu là gì", "Các dạng dữ liệu", "Ví dụ thực tế"],
                ),
                LessonSlide(
                    title="Ứng dụng",
                    bullets=["Thu thập", "Phân tích", "Đưa ra quyết định"],
                ),
            ],
            summary=["Hiểu dữ liệu", "Biết phân tích", "Áp dụng vào thực tế"],
        )

    def test_extracts_document_body(self):
        tex = r"\documentclass{article}\begin{document}Xin chào\end{document}"
        self.assertEqual(translated_body(tex), "Xin chào")

    def test_uses_current_flash_model_by_default_for_exports(self):
        calls = []

        class Models:
            async def generate_content(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(text="{}")

        fake_client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_EXPORT_MODEL": ""}),
            patch("app.services.content_exports.genai.Client", return_value=fake_client),
        ):
            asyncio.run(_generate_json_with_status("test prompt"))

        self.assertEqual(calls[0]["model"], "gemini-3.8-flash")

    def test_retries_503_and_uses_fallback_model(self):
        calls = []

        class Models:
            async def generate_content(self, **kwargs):
                calls.append(kwargs["model"])
                if kwargs["model"] == "gemini-3.8-flash":
                    error = RuntimeError("503 UNAVAILABLE")
                    error.code = 503
                    raise error
                return SimpleNamespace(text='{"ok": true}')

        fake_client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
        updates = []

        async def update_status(message, progress):
            updates.append((message, progress))

        with (
            patch.dict(os.environ, {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_EXPORT_MODEL": "gemini-3.8-flash",
                "GEMINI_EXPORT_FALLBACK_MODELS": "gemini-3.6-flash",
            }),
            patch("app.services.content_exports.genai.Client", return_value=fake_client),
            patch("app.services.content_exports.asyncio.sleep", new=AsyncMock()),
            patch("app.services.content_exports.random.uniform", return_value=0.5),
        ):
            result = asyncio.run(_generate_json_with_status("test prompt", update_status))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            calls,
            ["gemini-3.8-flash"] * 4 + ["gemini-3.6-flash"],
        )
        self.assertTrue(any("đang quá tải" in message for message, _ in updates))

    def test_uses_exponential_backoff_then_raises_friendly_overload(self):
        class Models:
            async def generate_content(self, **kwargs):
                error = RuntimeError("503 UNAVAILABLE")
                error.code = 503
                raise error

        fake_client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
        sleeps = []
        updates = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        async def update_status(message, progress):
            updates.append((message, progress))

        with (
            patch.dict(os.environ, {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_EXPORT_MODEL": "primary",
                "GEMINI_EXPORT_FALLBACK_MODELS": "",
            }),
            patch("app.services.content_exports.genai.Client", return_value=fake_client),
            patch("app.services.content_exports.asyncio.sleep", new=fake_sleep),
            patch("app.services.content_exports.random.uniform", return_value=0.25),
        ):
            with self.assertRaises(GeminiOverloadedError) as context:
                asyncio.run(_generate_json_with_status("test prompt", update_status))

        self.assertEqual(sleeps, [3.25, 6.25, 12.25])
        self.assertEqual(
            str(context.exception),
            "Máy chủ AI hiện đang quá tải tạm thời, vui lòng thử lại sau ít phút",
        )
        self.assertTrue(any("thử lại sau" in message for message, _ in updates))

    def test_exports_powerpoint_and_unicode_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pptx_path = Path(temp_dir) / "slides.pptx"
            pdf_path = Path(temp_dir) / "slides.pdf"
            create_pptx(self.deck, pptx_path)
            create_pdf(self.deck, pdf_path)

            self.assertEqual(len(Presentation(pptx_path).slides), 5)
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))

    def test_renders_captioned_scene_and_subtitle_time(self):
        scene = VideoScene(
            title="Phân tích dữ liệu",
            narration="Phân tích dữ liệu giúp chuyển thông tin thành quyết định.",
            caption="Tìm insight từ dữ liệu.",
            visual_keywords=["Phân tích", "Biểu đồ"],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "scene.png"
            _create_scene_image(scene, image_path, 1, 5)
            self.assertTrue(image_path.is_file())
        self.assertEqual(_srt_time(61.25), "00:01:01,250")

    def test_video_job_reports_failure_instead_of_remaining_at_ten_percent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "translated_document.tex"
            source_path.write_text(r"\begin{document}Nội dung\end{document}", encoding="utf-8")
            job_id = "test-video-failure"
            video_jobs[job_id] = {
                "job_id": job_id,
                "session_id": "test-session",
                "status": "processing",
                "progress": 10,
            }
            try:
                with patch(
                    "app.routers.exports.create_summary_video",
                    new=AsyncMock(side_effect=RuntimeError("Gemini unavailable")),
                ):
                    asyncio.run(
                        _render_video_job("test-session", job_id, source_path, Path(temp_dir) / "out")
                    )
                self.assertEqual(video_jobs[job_id]["status"], "failed")
                self.assertEqual(video_jobs[job_id]["progress"], 100)
                self.assertEqual(video_jobs[job_id]["message"], "Gemini unavailable")
                self.assertFalse(video_jobs[job_id]["retryable"])
            finally:
                video_jobs.pop(job_id, None)

    def test_video_job_marks_overload_as_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "translated_document.tex"
            source_path.write_text(r"\begin{document}Nội dung\end{document}", encoding="utf-8")
            job_id = "test-video-overloaded"
            video_jobs[job_id] = {
                "job_id": job_id,
                "session_id": "test-session",
                "status": "processing",
                "progress": 10,
            }
            try:
                with patch(
                    "app.routers.exports.create_summary_video",
                    new=AsyncMock(side_effect=GeminiOverloadedError()),
                ):
                    asyncio.run(
                        _render_video_job("test-session", job_id, source_path, Path(temp_dir) / "out")
                    )
                self.assertEqual(video_jobs[job_id]["status"], "failed")
                self.assertEqual(video_jobs[job_id]["error_code"], "AI_OVERLOADED")
                self.assertTrue(video_jobs[job_id]["retryable"])
                self.assertEqual(
                    video_jobs[job_id]["message"],
                    "Máy chủ AI hiện đang quá tải tạm thời, vui lòng thử lại sau ít phút",
                )
            finally:
                video_jobs.pop(job_id, None)

    def test_video_download_routes_return_file_and_keep_status_route_distinct(self):
        with tempfile.TemporaryDirectory(prefix="guest_export_", dir=GUEST_UPLOADS_DIR) as temp_dir:
            session_id = Path(temp_dir).name
            translated_file = Path(temp_dir) / "translated_document.tex"
            translated_file.write_text(
                r"\begin{document}Nội dung đã dịch\end{document}",
                encoding="utf-8",
            )
            job_id = "video-download-test"
            output_dir = Path(temp_dir) / "exports" / f"video_{job_id}"
            output_dir.mkdir(parents=True)
            video_content = b"video fixture"
            (output_dir / "summary_video.mp4").write_bytes(video_content)
            (output_dir / "summary_video.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
            guest_sessions[session_id] = {"session_dir": temp_dir}
            video_jobs[job_id] = {
                "job_id": job_id,
                "session_id": session_id,
                "status": "completed",
                "download_url": f"/api/exports/{session_id}/video/{job_id}/download",
                "subtitles_url": f"/api/exports/{session_id}/video/{job_id}.srt",
            }
            try:
                client = TestClient(app)
                status_response = client.get(f"/api/exports/{session_id}/video/{job_id}")
                download_response = client.get(video_jobs[job_id]["download_url"])
                legacy_download_response = client.get(
                    f"/api/exports/{session_id}/video/{job_id}.mp4"
                )
                subtitle_response = client.get(video_jobs[job_id]["subtitles_url"])

                self.assertEqual(status_response.status_code, 200)
                self.assertEqual(status_response.json()["job_id"], job_id)
                self.assertEqual(download_response.status_code, 200)
                self.assertEqual(download_response.headers["content-type"], "video/mp4")
                self.assertEqual(download_response.content, video_content)
                self.assertEqual(legacy_download_response.status_code, 200)
                self.assertEqual(legacy_download_response.content, video_content)
                self.assertEqual(subtitle_response.status_code, 200)
            finally:
                guest_sessions.pop(session_id, None)
                video_jobs.pop(job_id, None)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_renders_mp4_pipeline_with_local_speech_fixture(self):
        script = {
            "title": "Tóm tắt bài học",
            "scenes": [
                {
                    "title": f"Phần {index}",
                    "narration": (
                        "Nội dung bài học trình bày khái niệm, ví dụ, ứng dụng và các bước "
                        "thực hành để người học có thể hiểu và vận dụng kiến thức hiệu quả."
                    ),
                    "caption": "Nắm ý chính và áp dụng vào thực tế.",
                    "visual_keywords": ["Khái niệm", "Ứng dụng"],
                }
                for index in range(1, 6)
            ],
        }

        class LocalSpeechFixture:
            def __init__(self, text, voice):
                self.text = text
                self.voice = voice

            async def save(self, path):
                process = await asyncio.create_subprocess_exec(
                    shutil.which("ffmpeg"),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "0.8",
                    "-q:a",
                    "9",
                    "-acodec",
                    "libmp3lame",
                    path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await process.communicate()
                if process.returncode:
                    raise RuntimeError("Could not create local audio fixture.")

        async def run_video(temp_dir):
            updates = []

            async def update_status(message, progress):
                updates.append((message, progress))

            with (
                patch(
                    "app.services.video_export._generate_json_with_status",
                    new=AsyncMock(return_value=script),
                ),
                patch("app.services.video_export.edge_tts.Communicate", LocalSpeechFixture),
            ):
                duration, video_path = await create_summary_video(
                    r"\begin{document}Nội dung bài học\end{document}",
                    Path(temp_dir),
                    update_status,
                )
            self.assertGreater(duration, 0)
            self.assertTrue(video_path.is_file())
            self.assertGreater(video_path.stat().st_size, 0)
            self.assertTrue((Path(temp_dir) / "summary_video.srt").is_file())
            self.assertEqual(updates[-1][1], 100)

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(run_video(temp_dir))

    def test_slides_api_persists_deck_and_downloads_files(self):
        with tempfile.TemporaryDirectory(prefix="guest_export_", dir=GUEST_UPLOADS_DIR) as temp_dir:
            session_id = Path(temp_dir).name
            translated_file = Path(temp_dir) / "translated_document.tex"
            translated_file.write_text(
                r"\begin{document}Nội dung đã dịch\end{document}",
                encoding="utf-8",
            )
            guest_sessions[session_id] = {"session_dir": temp_dir}
            try:
                with patch(
                    "app.routers.exports.generate_lesson_deck",
                    new=AsyncMock(return_value=self.deck),
                ):
                    client = TestClient(app)
                    response = client.post(f"/api/exports/{session_id}/slides")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["title"], self.deck.title)

                client = TestClient(app)
                self.assertEqual(client.get(f"/api/exports/{session_id}/slides").status_code, 200)
                pptx_response = client.get(f"/api/exports/{session_id}/slides.pptx")
                pdf_response = client.get(f"/api/exports/{session_id}/slides.pdf")
                self.assertEqual(pptx_response.status_code, 200)
                self.assertEqual(pdf_response.status_code, 200)
                self.assertTrue(pptx_response.content.startswith(b"PK"))
                self.assertTrue(pdf_response.content.startswith(b"%PDF"))
            finally:
                guest_sessions.pop(session_id, None)

    def test_slides_api_returns_retryable_overload_message(self):
        with tempfile.TemporaryDirectory(prefix="guest_export_", dir=GUEST_UPLOADS_DIR) as temp_dir:
            session_id = Path(temp_dir).name
            translated_file = Path(temp_dir) / "translated_document.tex"
            translated_file.write_text(
                r"\begin{document}Nội dung đã dịch\end{document}",
                encoding="utf-8",
            )
            guest_sessions[session_id] = {"session_dir": temp_dir}
            try:
                with patch(
                    "app.routers.exports.generate_lesson_deck",
                    new=AsyncMock(side_effect=GeminiOverloadedError()),
                ):
                    response = TestClient(app).post(f"/api/exports/{session_id}/slides")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json()["detail"],
                    "Máy chủ AI hiện đang quá tải tạm thời, vui lòng thử lại sau ít phút",
                )
            finally:
                guest_sessions.pop(session_id, None)


if __name__ == "__main__":
    unittest.main()
