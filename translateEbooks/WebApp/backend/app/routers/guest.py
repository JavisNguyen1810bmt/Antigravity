import os
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pathlib import Path
import json
import shutil
import uuid
import time
import asyncio
import traceback

from app.config import GUEST_UPLOADS_DIR, GEMINI_API_KEY, guest_sessions
from app.core.pdf_extractor import extract_pages_and_images
from app.core.latex import (
    clean_latex_code,
    escape_latex_text,
    LATEX_TEMPLATE,
    compile_xelatex,
    inject_latex_content,
)
from app.services.translator import translate_text_stream

import pymupdf as fitz

router = APIRouter()

# --- GIỚI HẠN & CẤU HÌNH CHO GUEST ---
MAX_GUEST_PAGES = 30           # Tối đa 30 trang mỗi file cho Guest
SESSION_TTL_SECONDS = 3600    # Tự động dọn dẹp file sau 1 giờ (3600s)


def cleanup_old_sessions():
    """Hàm chạy ngầm dọn dẹp các session và thư mục rác quá hạn"""
    now = time.time()
    expired_ids = []
    
    for s_id, session in list(guest_sessions.items()):
        created_at = session.get("created_at", 0)
        if now - created_at > SESSION_TTL_SECONDS:
            expired_ids.append(s_id)
            
    for s_id in expired_ids:
        session = guest_sessions.pop(s_id, None)
        if session:
            s_dir = Path(session.get("session_dir", ""))
            if s_dir.exists():
                try:
                    shutil.rmtree(s_dir)
                    print(f"[Cleanup] Đã dọn dẹp session quá hạn: {s_id}")
                except Exception as e:
                    print(f"[Cleanup Error] Không thể xóa {s_dir}: {e}")


@router.get("/download/pdf/{session_id}")
async def download_pdf(session_id: str):
    session_dir = Path(GUEST_UPLOADS_DIR) / session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy thư mục phiên làm việc.")

    pdf_path = session_dir / "translated_document.pdf"
    if not pdf_path.exists():
        pdf_candidates = list(session_dir.glob("*.pdf"))
        if not pdf_candidates:
            raise HTTPException(status_code=404, detail="File PDF dịch chưa được tạo thành công.")
        pdf_path = pdf_candidates[0]

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{session_id}_translated_vi.pdf",
        content_disposition_type="attachment",
    )


@router.get("/download/tex/{session_id}")
async def download_tex(session_id: str):
    session_dir = Path(GUEST_UPLOADS_DIR) / session_id
    tex_path = session_dir / "translated_document.tex"
    if not tex_path.is_file():
        raise HTTPException(status_code=404, detail="File LaTeX dịch chưa được tạo.")

    return FileResponse(
        path=str(tex_path),
        media_type="application/x-tex",
        filename=f"{session_id}_translated_vi.tex",
        content_disposition_type="attachment",
    )


def _latex_error_message(session_dir: Path) -> str:
    log_path = session_dir / "xelatex.log"
    if not log_path.exists():
        return "Không tìm thấy xelatex.log."

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    errors = [line.strip() for line in lines if line.lstrip().startswith(("!", "Emergency stop"))]
    if errors:
        return " ".join(errors[-3:])[:1000]
    return "XeLaTeX không tạo được PDF. Vui lòng kiểm tra xelatex.log."


@router.post("/api/upload-guest-pdf")
async def upload_guest_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    chunk_size: int = Form(10)
):
    # Kích hoạt dọn dẹp file cũ ở background
    background_tasks.add_task(cleanup_old_sessions)

    if chunk_size < 1 or chunk_size > MAX_GUEST_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"chunk_size phải nằm trong khoảng từ 1 đến {MAX_GUEST_PAGES}.",
        )

    filename = (file.filename or "").lower()
    if not (filename.endswith(".pdf") or filename.endswith(".tex")):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ định dạng PDF hoặc TEX!")

    session_id = f"guest_{uuid.uuid4().hex[:8]}"
    session_dir = GUEST_UPLOADS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    is_tex = filename.endswith(".tex")
    file_path = session_dir / ("original.tex" if is_tex else "original.pdf")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    total_pages = 1
    parts = []

    if is_tex:
        parts.append({"id": 1, "name": "Toàn bộ file .tex", "start_page": 0, "end_page": 1, "status": "pending"})
    else:
        try:
            doc = fitz.open(file_path)
            total_pages = len(doc)
            doc.close()
        except Exception:
            shutil.rmtree(session_dir)
            raise HTTPException(status_code=400, detail="File PDF bị lỗi hoặc hỏng!")

        # TỐI ƯU 1: Giới hạn số trang tối đa cho Khách
        if total_pages > MAX_GUEST_PAGES:
            shutil.rmtree(session_dir)
            raise HTTPException(
                status_code=400, 
                detail=f"Phiên Khách chỉ hỗ trợ tối đa {MAX_GUEST_PAGES} trang/file. File của bạn có {total_pages} trang."
            )

        part_id = 1
        for start in range(0, total_pages, chunk_size):
            end = min(start + chunk_size, total_pages)
            parts.append({
                "id": part_id,
                "name": f"Phần {part_id} (Trang {start + 1} - {end})",
                "start_page": start,
                "end_page": end,
                "status": "pending"
            })
            part_id += 1

    # Lưu session kèm mốc thời gian
    guest_sessions[session_id] = {
        "filename": file.filename,
        "is_tex": is_tex,
        "file_path": str(file_path),
        "total_pages": total_pages,
        "session_dir": str(session_dir),
        "parts": parts,
        "created_at": time.time()
    }

    return {
        "status": "success", 
        "session_id": session_id, 
        "filename": file.filename, 
        "total_pages": total_pages, 
        "parts": parts
    }


@router.websocket("/ws/translate-guest/{session_id}")
async def websocket_guest_endpoint(websocket: WebSocket, session_id: str, api_key: str = None, parts: str = None):
    await websocket.accept()

    if session_id not in guest_sessions:
        await websocket.send_text(json.dumps({
            "type": "log",
            "message": f"[Lỗi] Phiên dịch {session_id} không tồn tại hoặc đã hết hạn!"
        }))
        await websocket.close()
        return

    session = guest_sessions[session_id]
    actual_api_key = api_key or GEMINI_API_KEY
    try:
        selected_parts = {int(p.strip()) for p in parts.split(",") if p.strip()} if parts else set()
    except ValueError:
        await websocket.send_text(json.dumps({
            "type": "log",
            "message": "[Lỗi] Danh sách part không hợp lệ.",
        }))
        await websocket.close(code=1008)
        return

    valid_part_ids = {part["id"] for part in guest_sessions[session_id]["parts"]}
    if selected_parts - valid_part_ids:
        await websocket.send_text(json.dumps({
            "type": "log",
            "message": "[Lỗi] Danh sách part chứa phần không tồn tại.",
        }))
        await websocket.close(code=1008)
        return

    session_dir = Path(session["session_dir"])
    output_tex_dir = session_dir / "chapters_tex"
    pictures_dir = session_dir / "Pictures"
    output_tex_dir.mkdir(parents=True, exist_ok=True)
    pictures_dir.mkdir(parents=True, exist_ok=True)

    input_file_path = Path(session["file_path"])
    merged_tex_path = session_dir / "translated_document.tex"

    try:
        if session.get("is_tex"):
            with open(input_file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            await websocket.send_text(json.dumps({"type": "status", "part": 1, "status": "translating"}))

            async def stream_callback(chunk):
                await websocket.send_text(json.dumps({"type": "log_append", "text": chunk}))

            translated_tex = await translate_text_stream(
                content,
                actual_api_key,
                stream_callback,
            )
            translated_body = clean_latex_code(translated_tex)
            if not translated_body:
                raise ValueError("Gemini không trả về nội dung bản dịch.")
            if "\\" not in translated_body:
                translated_body = escape_latex_text(translated_body)
            with open(merged_tex_path, "w", encoding="utf-8") as f:
                f.write(inject_latex_content(LATEX_TEMPLATE, translated_body) + "\n")
            await websocket.send_text(json.dumps({"type": "status", "part": 1, "status": "completed"}))
        else:
            for part in session["parts"]:
                p_id = part["id"]
                if selected_parts and p_id not in selected_parts:
                    continue

                part_tex_path = output_tex_dir / f"part_{p_id}.tex"
                
                # Trích xuất văn bản & ảnh
                await websocket.send_text(json.dumps({"type": "status", "part": p_id, "status": "extracting"}))
                extracted_text = extract_pages_and_images(input_file_path, part["start_page"], part["end_page"], pictures_dir)

                if not extracted_text.strip():
                    await websocket.send_text(json.dumps({"type": "status", "part": p_id, "status": "completed"}))
                    continue

                # Bắt đầu tiến trình dịch
                await websocket.send_text(json.dumps({"type": "status", "part": p_id, "status": "translating"}))

                async def stream_callback(chunk):
                    await websocket.send_text(json.dumps({"type": "log_append", "text": chunk}))

                # TỐI ƯU 2: Bắt lỗi API Gemini khi server bận (Lỗi 503 trong terminal)
                try:
                    translated_tex = await translate_text_stream(extracted_text, actual_api_key, stream_callback)
                except Exception as api_err:
                    err_msg = str(api_err)
                    if "503" in err_msg or "UNAVAILABLE" in err_msg:
                        await websocket.send_text(json.dumps({
                            "type": "log", 
                            "message": f"[Cảnh báo Part {p_id}] Gemini Server đang quá tải (503). Vui lòng thử lại sau ít phút."
                        }))
                    raise api_err

                # 1. Kiểm tra & Fallback trước
                if not translated_tex or not translated_tex.strip():
                    print(f"[Cảnh báo] Part {p_id} không nhận được bản dịch. Tự động giữ lại văn bản gốc.")
                    translated_tex = extracted_text

                # 2. Làm sạch mã LaTeX sau khi đã xác định được text
                cleaned_tex = clean_latex_code(translated_tex)
                if "\\" not in cleaned_tex:
                    cleaned_tex = escape_latex_text(cleaned_tex)
                with open(part_tex_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_tex + "\n")

                await websocket.send_text(json.dumps({"type": "status", "part": p_id, "status": "completed"}))

            # Ghép mã LaTeX các phần
            body_content = ""
            for part in session["parts"]:
                p_file = output_tex_dir / f"part_{part['id']}.tex"
                if p_file.exists():
                    with open(p_file, "r", encoding="utf-8") as pf:
                        body_content += pf.read() + "\n\n"

            full_tex = inject_latex_content(LATEX_TEMPLATE, body_content)
            with open(merged_tex_path, "w", encoding="utf-8") as f:
                f.write(full_tex)

        # Biên dịch PDF
        await websocket.send_text(json.dumps({"type": "log", "message": "[System] Đang biên dịch PDF bằng XeLaTeX..."}))
        success = await compile_xelatex(session_dir)

        if not success:
            await websocket.send_text(json.dumps({
                "type": "log",
                "message": f"[Lỗi] Biên dịch XeLaTeX thất bại: {_latex_error_message(session_dir)}"
            }))

        await websocket.send_text(json.dumps({
            "type": "done",
            "download_url": f"/download/pdf/{session_id}",
            "tex_url": f"/download/tex/{session_id}",
            "pdf_failed": not success
        }))

    except WebSocketDisconnect:
        print(f"[Guest WS] Ngắt kết nối từ phía client: {session_id}")
    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"[Guest WS Error]:\n{err_msg}")
        try:
            await websocket.send_text(json.dumps({
                "type": "log",
                "message": f"[Lỗi Xử Lý]: {str(e)}"
            }))
            await websocket.send_text(json.dumps({
                "type": "done",
                "download_url": f"/download/pdf/{session_id}",
                "tex_url": f"/download/tex/{session_id}",
                "pdf_failed": True
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass