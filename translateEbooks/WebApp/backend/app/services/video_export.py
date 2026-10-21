import asyncio
import shutil
from pathlib import Path
from typing import Awaitable, Callable

import edge_tts
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from app.services.content_exports import (
    GeminiOverloadedError,
    _generate_json_with_status,
    translated_body,
)


class VideoScene(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    narration: str = Field(min_length=20, max_length=500)
    caption: str = Field(min_length=5, max_length=150)
    visual_keywords: list[str] = Field(min_length=2, max_length=4)


class SummaryVideoScript(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    scenes: list[VideoScene] = Field(min_length=5, max_length=8)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise RuntimeError("Không tìm thấy font Unicode DejaVu Sans/Arial để render video.")
    return ImageFont.truetype(str(font_path), size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _create_scene_image(scene: VideoScene, path: Path, number: int, total: int) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height))
    draw_background = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / height
        color = (
            int(13 + 8 * ratio),
            int(24 + 16 * ratio),
            int(48 + 18 * ratio),
        )
        draw_background.line((0, y, width, y), fill=color)

    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse((950, -210, 1430, 270), fill=(59, 130, 246, 60))
    draw.ellipse((-180, 390, 330, 900), fill=(16, 185, 129, 40))
    draw.rounded_rectangle((64, 48, 1216, 670), radius=28, outline=(148, 163, 184, 75), width=2)

    small_font = _font(20)
    title_font = _font(42)
    keyword_font = _font(24)
    caption_font = _font(25)
    draw.text((100, 84), f"TÓM TẮT BÀI HỌC  •  {number:02d}/{total:02d}", font=small_font, fill="#34d399")

    title_lines = _wrap_text(draw, scene.title, title_font, 1050)
    title_y = 132
    for line in title_lines[:3]:
        draw.text((100, title_y), line, font=title_font, fill="#f8fafc")
        title_y += 56

    keyword_y = 300
    chip_x = 100
    for keyword in scene.visual_keywords:
        label = keyword[:18]
        bounds = draw.textbbox((0, 0), label, font=keyword_font)
        chip_width = bounds[2] + 44
        if chip_x + chip_width > 1170:
            keyword_y += 64
            chip_x = 100
        draw.rounded_rectangle(
            (chip_x, keyword_y, chip_x + chip_width, keyword_y + 48),
            radius=20,
            fill=(59, 130, 246, 70),
            outline=(96, 165, 250, 150),
            width=1,
        )
        draw.text((chip_x + 22, keyword_y + 10), label, font=keyword_font, fill="#bfdbfe")
        chip_x += chip_width + 18

    draw.rounded_rectangle((88, 490, 1192, 642), radius=18, fill=(15, 23, 42, 235))
    caption_lines = _wrap_text(draw, scene.caption, caption_font, 1020)
    caption_y = 515
    for line in caption_lines[:3]:
        draw.text((120, caption_y), line, font=caption_font, fill="#f8fafc")
        caption_y += 38

    image.save(path, format="PNG", optimize=True)


def _srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


async def _run_process(*command: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"Lệnh {Path(command[0]).name} thất bại: {detail}")
    return stdout.decode("utf-8", errors="replace")


async def _audio_duration(ffprobe: str, audio_path: Path) -> float:
    output = await _run_process(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    )
    try:
        return float(output.strip())
    except ValueError as exc:
        raise RuntimeError(f"Không đọc được thời lượng audio {audio_path.name}.") from exc


async def create_summary_video(
    tex: str,
    output_dir: Path,
    update_status: Callable[[str, int], Awaitable[None]],
) -> tuple[float, Path]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Thiếu FFmpeg/FFprobe. Cài theo backend/system-requirements.txt.")

    body = translated_body(tex)
    await update_status("Đang tạo kịch bản tóm tắt bằng Gemini...", 10)
    try:
        raw_script = await _generate_json_with_status(
            "Tạo kịch bản video tóm tắt bằng tiếng Việt từ nội dung LaTeX đã dịch. "
            "Không bịa thông tin ngoài nguồn. Thời lượng lời đọc mục tiêu 60-90 giây "
            "(khoảng 130-200 từ tiếng Việt). Chia thành 5-8 cảnh theo đúng thứ tự mạch lạc. "
            "Mỗi caption là một câu phụ đề ngắn, tối đa 150 ký tự, tóm tắt lời dẫn của cảnh. "
            "visual_keywords là 2-4 từ/cụm từ để minh họa bằng thẻ hình ảnh. "
            'Trả về JSON: {"title":"tiêu đề","scenes":[{"title":"tiêu đề cảnh",'
            '"narration":"lời dẫn", "caption":"phụ đề ngắn",'
            '"visual_keywords":["từ khóa 1","từ khóa 2"]}]}. '
            "Không đưa lệnh LaTeX vào câu trả lời.\n\n"
            f"NỘI DUNG NGUỒN:\n{body}",
            update_status,
        )
    except GeminiOverloadedError as exc:
        await update_status(str(exc), 10)
        raise
    script = SummaryVideoScript.model_validate(raw_script)
    word_count = sum(len(scene.narration.split()) for scene in script.scenes)
    if not 100 <= word_count <= 260:
        raise ValueError(
            f"Kịch bản có {word_count} từ, ngoài giới hạn 100-260 từ để đạt gần 60-90 giây."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    segments_dir = output_dir / "scenes"
    segments_dir.mkdir(exist_ok=True)
    durations: list[float] = []
    segment_paths: list[Path] = []
    srt_lines = []
    elapsed = 0.0

    for index, scene in enumerate(script.scenes, start=1):
        progress = 15 + int(55 * index / len(script.scenes))
        await update_status(f"Đang tạo giọng đọc và cảnh {index}/{len(script.scenes)}...", progress)
        audio_path = segments_dir / f"scene_{index:02d}.mp3"
        image_path = segments_dir / f"scene_{index:02d}.png"
        segment_path = segments_dir / f"scene_{index:02d}.mp4"

        await edge_tts.Communicate(
            text=scene.narration,
            voice="vi-VN-HoaiMyNeural",
        ).save(str(audio_path))
        _create_scene_image(scene, image_path, index, len(script.scenes))
        duration = await _audio_duration(ffprobe, audio_path)
        durations.append(duration)
        srt_lines.extend(
            [
                str(index),
                f"{_srt_time(elapsed)} --> {_srt_time(elapsed + duration)}",
                scene.narration,
                "",
            ]
        )
        elapsed += duration

        await _run_process(
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-b:a",
            "128k",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            str(segment_path),
        )
        segment_paths.append(segment_path)

    srt_path = output_dir / "summary_video.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    concat_file = output_dir / "segments.txt"
    concat_file.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in segment_paths),
        encoding="utf-8",
    )

    await update_status("Đang ghép các phân cảnh và xuất MP4...", 80)
    video_path = output_dir / "summary_video.mp4"
    await _run_process(
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(video_path),
    )
    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg kết thúc nhưng không tạo được file MP4.")
    await update_status("Video đã sẵn sàng.", 100)
    return sum(durations), video_path
