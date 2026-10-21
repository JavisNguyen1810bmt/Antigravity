import json
import os
import random
import re
import asyncio
from pathlib import Path
from typing import Annotated, Awaitable, Callable

from google import genai
from google.genai import types
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Inches, Pt
from pydantic import BaseModel, Field
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit

Bullet = Annotated[str, Field(min_length=3, max_length=240)]


class MissingGeminiKeyError(RuntimeError):
    pass


class GeminiOverloadedError(RuntimeError):
    def __init__(self):
        super().__init__(
            "Máy chủ AI hiện đang quá tải tạm thời, vui lòng thử lại sau ít phút"
        )


class LessonSlide(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    bullets: list[Bullet] = Field(min_length=3, max_length=5)


class LessonDeck(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    objectives: list[Bullet] = Field(min_length=2, max_length=6)
    slides: list[LessonSlide] = Field(min_length=2, max_length=16)
    summary: list[Bullet] = Field(min_length=3, max_length=5)


def translated_body(tex: str) -> str:
    body = tex
    begin = re.search(r"\\begin\s*\{\s*document\s*\}", tex)
    end = list(re.finditer(r"\\end\s*\{\s*document\s*\}", tex))
    if begin and end and end[-1].start() >= begin.end():
        body = tex[begin.end():end[-1].start()]
    if not body.strip():
        raise ValueError("Không tìm thấy nội dung bản dịch trong file LaTeX.")
    if len(body) > 120_000:
        raise ValueError("Nội dung bản dịch vượt quá giới hạn 120.000 ký tự để tạo học liệu.")
    return body.strip()


def _gemini_client() -> genai.Client:
    configured_keys = os.getenv("GEMINI_API_KEYS", "")
    if not configured_keys.strip():
        configured_keys = os.getenv("GEMINI_API_KEY", "")
    keys = [
        key.strip().strip("'\"")
        for key in re.split(r"[,\n]+", configured_keys)
        if key.strip()
    ]
    if not keys:
        raise MissingGeminiKeyError("Thiếu GEMINI_API_KEY hoặc GEMINI_API_KEYS để tạo học liệu.")
    return genai.Client(api_key=keys[0])


async def _generate_json_with_status(
    prompt: str,
    update_status: Callable[[str, int], Awaitable[None]] | None = None,
) -> dict:
    client = _gemini_client()
    primary_model = os.getenv("GEMINI_EXPORT_MODEL") or "gemini-3.8-flash"
    fallback_models = [
        model.strip()
        for model in os.getenv(
            "GEMINI_EXPORT_FALLBACK_MODELS",
            "gemini-3.6-flash,gemini-2.5-flash-lite",
        ).split(",")
        if model.strip() and model.strip() != primary_model
    ]
    models = list(dict.fromkeys([primary_model, *fallback_models]))
    response = None
    last_error: Exception | None = None
    last_progress = 10
    saw_overload = False
    retry_delays = (3, 6, 12)

    async def generate_with_heartbeat(model: str, attempt: int, model_index: int):
        nonlocal last_progress
        request = asyncio.create_task(
            client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
        )
        elapsed = 0
        try:
            while elapsed < 45:
                interval = min(5, 45 - elapsed)
                done, _ = await asyncio.wait({request}, timeout=interval)
                if done:
                    return request.result()
                elapsed += interval
                if update_status:
                    next_progress = min(10 + model_index * 4 + attempt * 2 + elapsed // 5, 20)
                    last_progress = max(last_progress, next_progress)
                    await update_status(
                        f"Đang chờ Gemini {model} tạo nội dung ({elapsed}s)...",
                        last_progress,
                    )
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            raise asyncio.TimeoutError
        finally:
            if not request.done():
                request.cancel()

    for model_index, model in enumerate(models):
        for attempt in range(len(retry_delays) + 1):
            try:
                if update_status:
                    stage = f"Đang tạo nội dung bằng Gemini {model}..."
                    if attempt:
                        stage = f"Gemini {model} đang bận; thử lại lần {attempt}/3..."
                    elif model_index:
                        stage = f"Đang chuyển sang model dự phòng {model}..."
                    progress = 10 + min(model_index * 3 + attempt, 8)
                    last_progress = max(last_progress, progress)
                    await update_status(stage, last_progress)

                response = await generate_with_heartbeat(model, attempt, model_index)
                break
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Gemini {model} không phản hồi trong 45 giây.")
            except Exception as exc:
                last_error = exc
                message = str(exc).upper()
                status_code = getattr(exc, "code", None)
                if status_code is None:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                try:
                    status_code = int(status_code)
                except (TypeError, ValueError):
                    status_code = None
                retryable = (
                    status_code in {408, 429, 500, 502, 503, 504}
                    or any(code in message for code in ("408", "429", "500", "502", "503", "504"))
                    or "UNAVAILABLE" in message
                    or "RESOURCE_EXHAUSTED" in message
                    or "DEADLINE_EXCEEDED" in message
                )
                saw_overload = saw_overload or (
                    status_code in {429, 503}
                    or "429" in message
                    or "503" in message
                    or "RESOURCE_EXHAUSTED" in message
                    or "UNAVAILABLE" in message
                )
                model_unavailable = status_code == 404 or "404" in message or "NOT_FOUND" in message
                if not retryable and not model_unavailable:
                    raise
                if model_unavailable:
                    break
            if response is not None:
                break
            if attempt < len(retry_delays):
                delay = retry_delays[attempt] + random.uniform(0, 1)
                if update_status:
                    await update_status(
                        f"Gemini {model} đang quá tải; thử lại sau {delay:.1f} giây "
                        f"(lần {attempt + 1}/3)...",
                        last_progress,
                    )
                await asyncio.sleep(delay)
            if response is not None:
                break
        if response is not None:
            break

    if response is None:
        last_message = str(last_error).upper() if last_error else ""
        last_status_code = getattr(last_error, "code", None)
        if last_status_code is None and last_error is not None:
            last_status_code = getattr(getattr(last_error, "response", None), "status_code", None)
        try:
            last_status_code = int(last_status_code)
        except (TypeError, ValueError):
            last_status_code = None
        if saw_overload or (
            last_status_code in {429, 503}
            or "429" in last_message
            or "503" in last_message
            or "RESOURCE_EXHAUSTED" in last_message
            or "UNAVAILABLE" in last_message
        ):
            raise GeminiOverloadedError from last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Không thể tạo nội dung bằng các model Gemini đã cấu hình.")
    if not response.text:
        raise ValueError("Mô hình không trả về nội dung.")
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ValueError("Mô hình trả về dữ liệu JSON không hợp lệ.") from exc


async def generate_lesson_deck(tex: str) -> LessonDeck:
    body = translated_body(tex)
    result = await _generate_json_with_status(
        "Hãy tạo bài giảng bằng tiếng Việt từ nội dung LaTeX đã dịch dưới đây. "
        "Không bịa thông tin ngoài nguồn. Trả về JSON đúng schema: "
        '{"title":"tiêu đề bài giảng","objectives":["2-6 mục tiêu học tập"],'
        '"slides":[{"title":"tiêu đề phần","bullets":["3-5 ý chính chi tiết"]}],'
        '"summary":["3-5 ý tổng kết"]}. '
        "Tạo từ 2 đến 16 slide nội dung, mỗi slide có 3-5 ý ngắn gọn, dễ trình bày. "
        "Không đưa lệnh LaTeX vào câu trả lời.\n\n"
        f"NỘI DUNG NGUỒN:\n{body}"
    )
    return LessonDeck.model_validate(result)


def save_deck(deck: LessonDeck, path: Path) -> None:
    path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")


def load_deck(path: Path) -> LessonDeck:
    return LessonDeck.model_validate_json(path.read_text(encoding="utf-8"))


def _pptx_text(slide, text: str, x, y, w, h, size: int, color: str, bold: bool = False):
    shape = slide.shapes.add_textbox(x, y, w, h)
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.LEFT
    run = paragraph.add_run()
    run.text = text
    run.font.name = "DejaVu Sans"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    return shape


def _new_pptx_slide(presentation: Presentation, title: str, items: list[str], index: int):
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    background = slide.background.fill
    background.solid()
    background.fore_color.rgb = RGBColor(15, 23, 42)
    _pptx_text(slide, title, Inches(0.7), Inches(0.45), Inches(11.9), Inches(0.9), 28, "60A5FA", True)
    shape = slide.shapes.add_textbox(Inches(1.0), Inches(1.6), Inches(11.1), Inches(5.2))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    for number, item in enumerate(items):
        paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
        paragraph.text = f"•  {item}"
        paragraph.font.name = "DejaVu Sans"
        paragraph.font.size = Pt(18)
        paragraph.font.color.rgb = RGBColor(248, 250, 252)
        paragraph.space_after = Pt(14)
    _pptx_text(slide, f"ANTIGRAVITY  •  {index:02d}", Inches(9.8), Inches(7.0), Inches(2.6), Inches(0.3), 9, "94A3B8")
    return slide


def create_pptx(deck: LessonDeck, path: Path) -> None:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    _new_pptx_slide(presentation, deck.title, ["Bài giảng được tạo từ nội dung bản dịch."], 1)
    _new_pptx_slide(presentation, "Mục tiêu bài học", deck.objectives, 2)
    for index, item in enumerate(deck.slides, start=3):
        _new_pptx_slide(presentation, item.title, item.bullets, index)
    _new_pptx_slide(presentation, "Tổng kết", deck.summary, len(presentation.slides) + 1)
    presentation.save(path)


def _register_pdf_font() -> str:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    font_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if font_path is None:
        raise RuntimeError("Không tìm thấy font Unicode DejaVu Sans/Arial để xuất PDF.")
    if "AntigravityUnicode" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("AntigravityUnicode", str(font_path)))
    return "AntigravityUnicode"


def _draw_pdf_slide(page: canvas.Canvas, title: str, items: list[str], number: int, font: str) -> None:
    width, height = 960, 540
    page.setFillColor(HexColor("#0f172a"))
    page.rect(0, 0, width, height, fill=1, stroke=0)
    page.setFillColor(HexColor("#60a5fa"))
    title_size = 23
    title_lines = simpleSplit(title, font, title_size, width - 96)
    title_y = height - 60
    for line in title_lines[:3]:
        page.setFillColor(HexColor("#60a5fa"))
        page.setFont(font, title_size)
        page.drawString(48, title_y, line)
        title_y -= 28
    page.setStrokeColor(HexColor("#334155"))
    page.line(48, title_y - 4, width - 48, title_y - 4)

    body_size = 16
    body_lines = []
    available_height = title_y - 45 - 50
    for candidate_size in (16, 14, 12, 10):
        candidate_lines = [
            simpleSplit(item, font, candidate_size, width - 125)
            for item in items
        ]
        line_height = candidate_size * 1.4
        needed_height = sum(len(lines) * line_height + 12 for lines in candidate_lines)
        if needed_height <= available_height:
            body_size = candidate_size
            body_lines = candidate_lines
            break
    if not body_lines:
        body_lines = [
            simpleSplit(item, font, body_size, width - 125)
            for item in items
        ]

    cursor_y = title_y - 45
    line_height = body_size * 1.4
    for lines in body_lines:
        page.setFillColor(HexColor("#34d399"))
        page.circle(62, cursor_y + 4, 4, fill=1, stroke=0)
        page.setFillColor(HexColor("#f8fafc"))
        page.setFont(font, body_size)
        for line in lines:
            page.drawString(82, cursor_y, line)
            cursor_y -= line_height
        cursor_y -= 12

    page.setFillColor(HexColor("#94a3b8"))
    page.setFont(font, 9)
    page.drawRightString(width - 48, 28, f"ANTIGRAVITY  •  {number:02d}")
    page.showPage()


def create_pdf(deck: LessonDeck, path: Path) -> None:
    font = _register_pdf_font()
    page = canvas.Canvas(str(path), pagesize=(960, 540), pageCompression=1)
    _draw_pdf_slide(page, deck.title, ["Bài giảng được tạo từ nội dung bản dịch."], 1, font)
    _draw_pdf_slide(page, "Mục tiêu bài học", deck.objectives, 2, font)
    for index, slide in enumerate(deck.slides, start=3):
        _draw_pdf_slide(page, slide.title, slide.bullets, index, font)
    _draw_pdf_slide(page, "Tổng kết", deck.summary, len(deck.slides) + 3, font)
    page.save()
