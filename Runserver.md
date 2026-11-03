1. Tạo môi trường ảo (đặt tên là venv)

Linux / macOS:

Bash
python -m venv venv
Windows:

PowerShell
python -m venv venv
2. Kích hoạt môi trường ảo

Linux / macOS (Bash/Zsh):

Bash
source venv/bin/activate
(Nếu dùng Fish shell: source venv/bin/activate.fish)

Windows (PowerShell):

PowerShell
.\venv\Scripts\Activate.ps1
(Nếu dùng CMD: venv\Scripts\activate.bat)

3. Cài đặt Uvicorn
Sau khi kích hoạt (đầu dòng lệnh xuất hiện chữ (venv)), chạy lệnh cài đặt bản đầy đủ (kèm các thư viện tăng tốc hiệu năng như uvloop, httptools):

Bash
pip install "uvicorn[standard]"
(Nếu dự án đã có sẵn file thư viện thì chạy thêm: pip install -r requirements.txt)

4. Chạy thử server Uvicorn
Giả sử file chạy chính của bạn tên là main.py và biến khởi tạo app (FastAPI/Starlette) là app:

Bash
uvicorn main:app --reload
--reload: Tự động khởi động lại server mỗi khi bạn sửa code.

Để thoát môi trường ảo khi không dùng nữa, gõ lệnh: deactivate.
