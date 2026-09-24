import os
import re
import asyncio
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError


def _chunk_text(chunk) -> str:
    """Trích xuất text an toàn từ SDK chunk, bỏ qua phần thought của Gemini 2.5."""
    if not chunk:
        return ""
    
    try:
        text = getattr(chunk, "text", None)
        if text:
            return text
    except Exception:
        pass

    parts = []
    for candidate in getattr(chunk, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if content and getattr(content, "parts", None):
            for part in content.parts:
                # Bỏ qua phần suy luận (thought) của model 2.5 Flash
                if getattr(part, "thought", False):
                    continue
                if getattr(part, "text", None):
                    parts.append(part.text)
    return "".join(parts)


def _get_finish_reason(chunk) -> str:
    if not chunk or not getattr(chunk, "candidates", None):
        return "NO_CANDIDATES"
    reasons = [
        str(c.finish_reason)
        for c in chunk.candidates
        if getattr(c, "finish_reason", None)
    ]
    return ", ".join(reasons) if reasons else "UNKNOWN"


async def translate_text_stream(text: str, api_key: str, websocket_callback):
    actual_key = api_key or os.getenv("GEMINI_API_KEY")
    if not actual_key:
        raise ValueError("Thiếu API Key Gemini! Vui lòng kiểm tra file .env hoặc nhập key.")

    clean_input = text.strip() if text else ""
    print(f"\n[DEBUG] Độ dài văn bản đầu vào: {len(clean_input)} ký tự")
    
    # Nếu PDF đọc ra chuỗi rỗng -> Trả lại text gốc ngay
    if not clean_input:
        print("[LỖI ĐẦU VÀO] Văn bản bóc tách từ PDF bị RỖNG!")
        return text

    client = genai.Client(api_key=actual_key)

    system_instruction = (
        "Bạn là cỗ máy dịch thuật tự động sang Tiếng Việt dành cho tài liệu LaTeX.\n"
        "YÊU CẦU BẮT BUỘC:\n"
        "1. Chỉ trả về mã LaTeX đã dịch, không giải thích gì thêm.\n"
        "2. Tuyệt đối không thêm thẻ ```latex hay ``` xung quanh.\n"
        "3. Không thêm cấu trúc \\begin{document} hay \\end{document}.\n"
        "4. Giữ nguyên toàn bộ mã lệnh, tên biến, và các môi trường mã nguồn (như verbatim)."
    )

    models_to_try = ['gemini-3.6-flash']

    for model_index, model_name in enumerate(models_to_try):
        max_retries = 3
        for attempt in range(max_retries):
            response_text = ""
            last_chunk = None

            try:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                )

                response = await client.aio.models.generate_content_stream(
                    model=model_name,
                    contents=clean_input,
                    config=config
                )

                async for chunk in response:
                    last_chunk = chunk
                    chunk_text = _chunk_text(chunk)
                    if chunk_text:
                        response_text += chunk_text
                        await websocket_callback(chunk_text)

                clean_translated = response_text.replace("```latex", "").replace("```", "").strip()

                if not clean_translated:
                    reason = _get_finish_reason(last_chunk)
                    print(f"[Cảnh báo] Model {model_name} trả về rỗng (Reason: {reason})")
                    raise ValueError(f"Phản hồi rỗng ({reason})")
                
                print(f"[Thành công] Đã dịch xong bằng {model_name} ({len(clean_translated)} ký tự)")
                return clean_translated

            except (ClientError, ServerError, APIError, asyncio.TimeoutError, ValueError) as e:
                err_str = str(e)
                print(f"[Lỗi xử lý - {model_name} Lần {attempt + 1}]: {err_str}")

                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    match = re.search(r"retryDelay['\"]:\s*['\"](\d+)s['\"]", err_str)
                    wait_time = int(match.group(1)) if match else 10
                    await websocket_callback(
                        f"\n[Hệ thống] {model_name} chạm Quota (429). Chuyển model dự phòng sau {min(wait_time, 5)}s...\n"
                    )
                    await asyncio.sleep(min(wait_time, 5))
                    break

                elif ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)
                    await websocket_callback(
                        f"\n[Hệ thống] Server bận ({model_name}), thử lại lần {attempt + 2}/{max_retries}...\n"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    if model_index == len(models_to_try) - 1 and attempt == max_retries - 1:
                        print("[Fallback] Tất cả model thất bại. Giữ lại văn bản gốc.")
                        return text
                    break

    return text