from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# API key must be supplied by the environment; never keep credentials in source.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Repository's translateEbooks directory:
# app/ -> backend/ -> WebApp/ -> translateEbooks/
BASE_DIR = Path(__file__).resolve().parents[3]

# Directories
#EBOOK_DIR = BASE_DIR / "translateEbooks" / "JavaProgrammingEbook"
OUTPUT_DIR = BASE_DIR / "JavaProgrammingEbook" / "JavaProgramming_outputEbook"
ORIGINAL_DIR = BASE_DIR / "JavaProgrammingEbook" / "JavaProgramming_chaptersPDF"
GUEST_UPLOADS_DIR = BASE_DIR / "guest_uploads"

# Tạo folder nếu chưa tồn tại
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
GUEST_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Shared Memory Data
guest_sessions = {}