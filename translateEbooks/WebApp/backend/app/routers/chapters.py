from pathlib import Path
import re

from fastapi import APIRouter, HTTPException

from app.config import ORIGINAL_DIR

router = APIRouter(prefix="/api/chapters", tags=["chapters"])

_PLAN_DIR = ORIGINAL_DIR.parent / "devPlan"
_CHAPTER_PATTERN = re.compile(r"^Chapter_(?P<number>\d+|0_Intro|other)\.pdf$", re.IGNORECASE)
_PART_PATTERN = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Phần\s+(?P<number>\d+)", re.IGNORECASE | re.MULTILINE)


def _chapter_id(path: Path) -> str:
    match = _CHAPTER_PATTERN.match(path.name)
    if not match:
        raise ValueError(f"Unsupported chapter filename: {path.name}")
    number = match.group("number").lower().replace("_intro", "_intro")
    return f"chapter_{number}"


def _chapter_name(chapter_id: str) -> str:
    suffix = chapter_id.removeprefix("chapter_")
    return "Introduction" if suffix == "0_intro" else f"Chapter {suffix.replace('_', ' ').title()}"


def _parts_for(chapter_id: str) -> list[dict]:
    suffix = chapter_id.removeprefix("chapter_")
    plan_name = str(int(suffix)) if suffix.isdigit() else suffix
    plan_path = _PLAN_DIR / f"translateChapter{plan_name}.md"
    parts = []
    if plan_path.is_file():
        content = plan_path.read_text(encoding="utf-8")
        part_numbers = list(dict.fromkeys(int(match.group("number")) for match in _PART_PATTERN.finditer(content)))
        parts = [
            {"id": number, "name": f"Part {number}", "status": "pending"}
            for number in part_numbers
        ]
    return parts


@router.get("")
async def list_chapters():
    chapters = []
    for pdf_path in sorted(ORIGINAL_DIR.glob("Chapter_*.pdf")):
        match = _CHAPTER_PATTERN.match(pdf_path.name)
        if match:
            chapter_id = _chapter_id(pdf_path)
            chapters.append({
                "id": chapter_id,
                "name": _chapter_name(chapter_id),
                "status": "pending",
                "filename": pdf_path.name,
            })
    return chapters


@router.get("/{chapter_id}/plan")
async def chapter_plan(chapter_id: str):
    candidates = list(ORIGINAL_DIR.glob("Chapter_*.pdf"))
    if not any(_chapter_id(path) == chapter_id for path in candidates):
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {
        "id": chapter_id,
        "name": _chapter_name(chapter_id),
        "parts": _parts_for(chapter_id),
    }