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
    # The model may repeat a preamble; these packages conflict with XeLaTeX.
    text = re.sub(
        r"(?m)^\s*\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{(?:inputenc|fontenc|babel)\}\s*;?\s*$",
        "",
        text,
    )
    text = re.sub(r"(?m)^\s*\\(?:setmainfont|setsansfont|setmonofont)\s*\{[^}]*\}\s*$", "", text)
    return text.strip()

async def compile_xelatex(work_dir: Path, tex_filename: str = "translated_document.tex") -> bool:
    """Gọi lệnh biên dịch XeLaTeX."""
    tex_path = work_dir / tex_filename
    if not tex_path.exists():
        return False

    xelatex = shutil.which("xelatex")
    if not xelatex:
        (work_dir / "xelatex.log").write_text(
            "Không tìm thấy lệnh xelatex. "
            "Cài các dependency trong system-requirements.txt.\n",
            encoding="utf-8",
        )
        return False

    pdf_path = work_dir / tex_filename.replace(".tex", ".pdf")
    log_path = work_dir / "xelatex.log"
    output = bytearray()
    success = False
    pdf_path.unlink(missing_ok=True)

    try:
        for _ in range(2):
            proc = await asyncio.create_subprocess_exec(
                xelatex,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                f"-output-directory={work_dir}",
                str(tex_path),
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output.extend(stdout)
            output.extend(b"\n")
            output.extend(stderr)
            if proc.returncode != 0:
                return False

        success = pdf_path.exists() and pdf_path.stat().st_size > 0
        return success
    except (FileNotFoundError, OSError):
        return False
    finally:
        # Keep a diagnostic log only when compilation failed; intermediate
        # files must not be exposed as downloadable session output.
        for suffix in (".aux", ".log", ".out"):
            for artifact in work_dir.glob(f"*{suffix}"):
                artifact.unlink(missing_ok=True)
        if success:
            log_path.unlink(missing_ok=True)
        else:
            log_path.write_bytes(bytes(output) or b"XeLaTeX compilation failed.\n")
