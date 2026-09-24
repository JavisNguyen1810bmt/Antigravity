from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import OUTPUT_DIR, ORIGINAL_DIR, GUEST_UPLOADS_DIR
from app.routers import chapters, guest

app = FastAPI(title="Antigravity Ebook Translator API")

# Cấu hình CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount các Static Directory
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/original", StaticFiles(directory=str(ORIGINAL_DIR)), name="original")
app.mount("/guest_files", StaticFiles(directory=str(GUEST_UPLOADS_DIR)), name="guest_files")

# Include Routers
app.include_router(guest.router)
app.include_router(chapters.router)



if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)