import re
import asyncio
import shutil
from pathlib import Path
from typing import Final

LATEX_TEMPLATE: Final[str] = r"""\documentclass[12pt]{article}
\usepackage[a4paper,margin=1in]{geometry}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{hyperref}
\usepackage{fontspec}
\setmainfont{DejaVu Sans}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{listings}

\begin{document}
\end{document}
"""
LATEX_PREAMBLE = LATEX_TEMPLATE

_LATEX_SPECIAL_CHARS = str.maketrans({
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
})


def escape_latex_text(text: str) -> str:
    """Escape plain translated text before inserting it into a LaTeX document."""
    return text.translate(_LATEX_SPECIAL_CHARS)


def inject_latex_content(template: str, content: str) -> str:
    """Replace the document body while preserving the template preamble."""
    begin = re.search(r"\\begin\{document\}", template)
    end = list(re.finditer(r"\\end\{document\}", template))
    if begin is None or not end:
        raise ValueError("Template LaTeX phải chứa \\begin{document} và \\end{document}.")

    end_match = end[-1]
    return f"{template[:begin.end()]}\n{content.strip()}\n{template[end_match.start():]}"


def clean_latex_code(text: str) -> str:
    """Làm sạch thẻ Markdown và đoạn dư thừa do AI trả về."""
    text = re.sub(r"```(?:latex)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")
    text = re.sub(r"\\documentclass.*?(?=\\begin\{document\}|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"\\begin\{document\}", "", text)
    text = re.sub(r"\\end\{document\}", "", text)
    # Xóa các package xung đột font với XeLaTeX
    text = re.sub(
        r"(?m)^\s*\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{(?:inputenc|fontenc|babel)\}\s*;?\s*$",
        "",
        text,
    )
    text = re.sub(r"(?m)^\s*\\(?:setmainfont|setsansfont|setmonofont)\s*\{[^}]*\}\s*$", "", text)
    return text.strip()
def _latex_error_message(work_dir: Path) -> str:
    log_path = work_dir / "xelatex.log"
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8", errors="ignore").strip()
        if content:
            return content[:300]  # Lấy 300 ký tự lỗi đầu tiên
    return "XeLaTeX không tạo được PDF. Vui lòng kiểm tra log."

async def compile_xelatex(work_dir: Path, tex_filename: str = "main.tex") -> bool:
    """Gọi lệnh biên dịch XeLaTeX ép tạo PDF ngay cả khi có lỗi nhỏ."""
    tex_path = work_dir / tex_filename
    if not tex_path.exists():
        # Tìm file .tex bất kỳ trong thư mục nếu tên không trùng main.tex
        full_tex_files = [f for f in work_dir.glob("*.tex") if not f.name.startswith("part_")]
        if full_tex_files:
            tex_path = full_tex_files[0]
        else:
            return False

    xelatex = shutil.which("xelatex")
    if not xelatex:
        (work_dir / "xelatex.log").write_text(
            "Không tìm thấy lệnh xelatex trong hệ thống.\n",
            encoding="utf-8",
        )
        return False

    pdf_path = tex_path.with_suffix(".pdf")
    log_path = work_dir / "xelatex.log"
    pdf_path.unlink(missing_ok=True)

    try:
        # ĐÃ XÓA `-halt-on-error` để XeLaTeX bỏ qua lỗi cú pháp nhỏ và tiếp tục sinh PDF
        cmd = [
            xelatex,
            "-interaction=nonstopmode",
            "-file-line-error",
            f"-output-directory={work_dir}",
            str(tex_path),
        ]

        # Biên dịch 2 lượt (để cập nhật index / TOC / reference nếu có)
        for _ in range(2):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

        # Kiểm tra xem PDF đã được xuất thành công chưa
        success = pdf_path.exists() and pdf_path.stat().st_size > 0

        # Nếu thất bại, ghi log chi tiết từ XeLaTeX ra để debug
        if not success:
            tex_log = tex_path.with_suffix(".log")
            if tex_log.exists():
                log_content = tex_log.read_text(encoding="utf-8", errors="ignore")
                # Trích xuất các dòng lỗi bắt đầu bằng dấu !
                error_lines = [line for line in log_content.splitlines() if line.startswith("!")]
                log_path.write_text("\n".join(error_lines) or log_content[-2000:], encoding="utf-8")
            else:
                log_path.write_text((stdout + stderr).decode("utf-8", errors="ignore"), encoding="utf-8")

        return success

    except (FileNotFoundError, OSError) as e:
        log_path.write_text(f"Lỗi thực thi Subprocess: {str(e)}", encoding="utf-8")
        return False
    finally:
        # Chỉ dọn dẹp các file rác không cần thiết, GIỮ LẠI file .log để đọc lỗi
        for suffix in (".aux", ".out", ".toc", ".nav", ".snm"):
            for artifact in work_dir.glob(f"*{suffix}"):
                artifact.unlink(missing_ok=True)