import os
import re
import asyncio
import threading
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError
from dotenv import load_dotenv

load_dotenv()

class KeyManager:
    def __init__(self, keys=None):
        if keys is None:
            keys_str = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
            configured_keys = re.split(r"[,\n]+", keys_str)
            if not any(key.strip() for key in configured_keys):
                configured_keys = [os.getenv("GEMINI_API_KEY", "")]
        elif isinstance(keys, str):
            configured_keys = re.split(r"[,\n]+", keys)
        else:
            configured_keys = keys

        #self.keys = list(dict.fromkeys(key.strip() for key in configured_keys if key.strip()))
        self.keys = [k.strip().strip("'\"") for k in keys_str.split(",") if k.strip()]
        self._key_index = 0
        self._lock = threading.Lock()

    def get_key(self) -> str:
        with self._lock:
            return self.keys[self._key_index]

    def get_next_key(self) -> str:
        with self._lock:
            key = self.keys[self._key_index]
            self._key_index = (self._key_index + 1) % len(self.keys)
            return key

    def rotate_key(self) -> str:
        with self._lock:
            if self.keys:
                self._key_index = (self._key_index + 1) % len(self.keys)
                return self.keys[self._key_index]
        return ""


key_manager = KeyManager()

def split_text_into_chunks(text: str, max_chars: int = 3500) -> list[str]:
    """Chia nhỏ văn bản thành các chunk khoảng 3500 ký tự theo dòng để tránh lỗi 503."""
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_length = 0

    for line in lines:
        if current_length + len(line) > max_chars and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


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


async def translate_text_stream(text: str, api_key: str | None, websocket_callback):
    actual_key = api_key.strip() if api_key and api_key.strip() else None
    if not actual_key and not key_manager.keys:
        raise ValueError(
            "Thiếu API key Gemini! Hãy cấu hình GEMINI_API_KEY hoặc GEMINI_API_KEYS, "
            "hoặc nhập API key."
        )

    clean_input = text.strip() if text else ""
    print(f"\n[DEBUG] Độ dài văn bản đầu vào: {len(clean_input)} ký tự")

    # Nếu PDF đọc ra chuỗi rỗng -> Trả lại text gốc ngay
    if not clean_input:
        print("[LỖI ĐẦU VÀO] Văn bản bóc tách từ PDF bị RỖNG!")
        return text
    # Tu dong chia part
    if len(clean_input) > 4000:
        print(f"[Hệ thống] Văn bản dài ({len(clean_input)} ký tự). Đang tự động chia nhỏ...")
        sub_chunks = split_text_into_chunks(clean_input, max_chars=3500)
        translated_results = []

        for idx, sub_chunk in enumerate(sub_chunks):
            print(f"[Hệ thống] Đang dịch Sub-chunk {idx + 1}/{len(sub_chunks)} ({len(sub_chunk)} ký tự)...")
            res = await _translate_single_chunk(sub_chunk, actual_key, websocket_callback)
            translated_results.append(res)
            # Nghỉ 1 giây giữa các sub-chunk để tránh 503 / 429
            await asyncio.sleep(1.0)

        return "\n\n".join(translated_results)
    else:
        return await _translate_single_chunk(clean_input, actual_key, websocket_callback)

async def _translate_single_chunk(text: str, api_key: str | None, websocket_callback) -> str:

    system_instruction = (
        "Bạn là cỗ máy dịch thuật tự động sang Tiếng Việt dành cho tài liệu LaTeX.\n"
        "YÊU CẦU BẮT BUỘC:\n"
        "1. Chỉ trả về mã LaTeX đã dịch, không giải thích gì thêm.\n"
        "2. Tuyệt đối không thêm thẻ ```latex hay ``` xung quanh.\n"
        "3. Không thêm cấu trúc \\begin{document} hay \\end{document}.\n"
        "4. Giữ nguyên toàn bộ mã lệnh, tên biến, và các môi trường mã nguồn."
    )

    models_to_try = ['gemini-3.6-flash']

    for model_name in models_to_try:
        max_retries = 4
        key_count = 1 if api_key else len(key_manager.keys)
        max_attempts = max_retries * key_count
        for attempt in range(max_attempts):
            response_text = ""
            last_chunk = None
            current_key = api_key or key_manager.get_next_key()

            try:
                client = genai.Client(api_key=current_key)
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                )

                response = await client.aio.models.generate_content_stream(
                    model=model_name,
                    contents=text,
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
                    raise ValueError(f"Phản hồi rỗng ({reason})")
                
                print(f"[Thành công] Đã dịch xong bằng {model_name} ({len(clean_translated)} ký tự)")
                return clean_translated

            except (ClientError, ServerError, APIError, asyncio.TimeoutError, ValueError) as e:
                err_str = str(e)
                print(f"[Lỗi xử lý - {model_name} Lần {attempt + 1}]: {err_str}")
                normalized_error = err_str.upper()
                is_quota_error = "429" in normalized_error or "RESOURCE_EXHAUSTED" in normalized_error
                is_key_error = any(
                    marker in normalized_error
                    for marker in ("API_KEY_INVALID", "API KEY NOT VALID", "401", "403", "PERMISSION_DENIED")
                )

                if not api_key and len(key_manager.keys) > 1 and (is_quota_error or is_key_error):
                    await websocket_callback(
                        "\n[Hệ thống] API key hiện tại không khả dụng, đang thử key tiếp theo...\n"
                    )
                    if (attempt + 1) % key_count == 0 and is_quota_error:
                        await asyncio.sleep(5)
                    continue

                if "503" in normalized_error or "UNAVAILABLE" in normalized_error:
                    wait_time = 4 * (attempt + 1)  # Thử lại sau 4s, 8s, 12s...
                    await websocket_callback(
                        f"\n[Hệ thống] Server Gemini đang bận, thử lại lần {attempt + 2}/{max_attempts} sau {wait_time}s...\n"
                    )
                    await asyncio.sleep(wait_time)
                elif is_quota_error:
                    await asyncio.sleep(5)
                elif isinstance(e, ValueError) and attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                else:
                    raise

                if attempt == max_attempts - 1:
                    raise

    raise RuntimeError("Không thể dịch nội dung bằng các API key Gemini đã cấu hình.")