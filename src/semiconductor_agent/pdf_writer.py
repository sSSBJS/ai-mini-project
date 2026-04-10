from __future__ import annotations

import shutil
import subprocess
import re
import textwrap
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


def write_simple_pdf(lines: Iterable[str], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styled_lines = _prepare_lines(list(lines))
    escaped_pages = _paginate(styled_lines, max_lines=32)
    font_object = "1 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    bold_font_object = "2 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n"

    objects = [font_object, bold_font_object]
    page_object_numbers = []
    content_object_numbers = []
    next_object_number = 3

    for page_index, page_lines in enumerate(escaped_pages, start=1):
        content_stream = _build_content_stream(page_lines, page_index, len(escaped_pages))
        content_object_numbers.append(next_object_number)
        objects.append(
            "%d 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n"
            % (next_object_number, len(content_stream.encode("utf-8")), content_stream)
        )
        next_object_number += 1

        page_object_numbers.append(next_object_number)
        objects.append(
            "%d 0 obj\n<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 1 0 R /F2 2 0 R >> >> /Contents %d 0 R >>\nendobj\n"
            % (next_object_number, 0, content_object_numbers[-1])
        )
        next_object_number += 1

    pages_object_number = next_object_number
    kids = " ".join("%d 0 R" % number for number in page_object_numbers)
    objects.append(
        "%d 0 obj\n<< /Type /Pages /Kids [%s] /Count %d >>\nendobj\n"
        % (pages_object_number, kids, len(page_object_numbers))
    )
    next_object_number += 1

    catalog_object_number = next_object_number
    objects.append(
        "%d 0 obj\n<< /Type /Catalog /Pages %d 0 R >>\nendobj\n"
        % (catalog_object_number, pages_object_number)
    )

    patched_objects = []
    for obj in objects:
        patched_objects.append(obj.replace("/Parent 0 0 R", "/Parent %d 0 R" % pages_object_number))

    pdf_body = "%PDF-1.4\n"
    offsets = []
    for obj in patched_objects:
        offsets.append(len(pdf_body.encode("utf-8")))
        pdf_body += obj

    xref_start = len(pdf_body.encode("utf-8"))
    pdf_body += "xref\n0 %d\n" % (len(patched_objects) + 1)
    pdf_body += "0000000000 65535 f \n"
    for offset in offsets:
        pdf_body += "%010d 00000 n \n" % offset
    pdf_body += (
        "trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%EOF"
        % (len(patched_objects) + 1, catalog_object_number, xref_start)
    )

    output_path.write_bytes(pdf_body.encode("utf-8"))
    return output_path


def write_html_pdf(html_path: Path, output_path: Path) -> Optional[Path]:
    chrome = _find_chrome_executable()
    if not chrome:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--run-all-compositor-stages-before-draw",
        "--print-to-pdf=%s" % output_path,
        "--no-pdf-header-footer",
        html_path.resolve().as_uri(),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        return None
    return output_path if output_path.exists() else None


def _paginate(lines: List[str], max_lines: int) -> List[List[str]]:
    pages = []
    for index in range(0, len(lines), max_lines):
        pages.append(lines[index : index + max_lines])
    return pages or [[]]


def _build_content_stream(lines: List[Tuple[str, str]], page_index: int, page_count: int) -> str:
    y_position = 760
    stream_lines = []
    # Soft page background header band
    stream_lines.append("0.95 0.97 1 rg")
    stream_lines.append("40 730 532 38 re f")
    stream_lines.append("0.11 0.24 0.55 RG")
    stream_lines.append("40 730 532 38 re S")
    # Header title
    stream_lines.append("BT")
    stream_lines.append("/F2 15 Tf")
    stream_lines.append("1 1 1 rg")
    stream_lines.append("1 0 0 1 52 746 Tm (SK hynix Technology Strategy Report) Tj")
    stream_lines.append("ET")
    y_position = 705

    for raw_line, line_type in lines:
        line = _strip_html(raw_line)
        if not line.strip():
            y_position -= 10
            continue
        escaped = _escape_pdf_text(line)
        font, size, color, indent = _style_for_line(line, line_type)
        stream_lines.append("BT")
        stream_lines.append("/%s %s Tf" % (font, size))
        stream_lines.append("%s rg" % color)
        stream_lines.append("1 0 0 1 %d %d Tm (%s) Tj" % (indent, y_position, escaped))
        stream_lines.append("ET")
        if line_type == "h1":
            y_position -= 24
        elif line_type == "h2":
            y_position -= 20
        elif line_type == "h3":
            y_position -= 18
        else:
            y_position -= 15

    # Footer
    stream_lines.append("0.75 0.80 0.90 RG")
    stream_lines.append("40 32 532 0.8 re S")
    stream_lines.append("BT")
    stream_lines.append("/F1 9 Tf")
    stream_lines.append("0.38 0.44 0.54 rg")
    stream_lines.append("1 0 0 1 50 18 Tm (Page %d / %d) Tj" % (page_index, page_count))
    stream_lines.append("ET")
    return "\n".join(stream_lines)


def _prepare_lines(lines: List[str]) -> List[Tuple[str, str]]:
    prepared = []
    for raw in lines:
        line_type = _classify_line(raw)
        wrapped = _wrap_pdf_line(raw, line_type)
        for chunk in wrapped:
            prepared.append((chunk, line_type))
    return prepared


def _classify_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("# "):
        return "h1"
    if stripped.startswith("## "):
        return "h2"
    if stripped.startswith("### "):
        return "h3"
    if stripped.startswith("|"):
        return "table"
    if stripped.startswith("- "):
        return "bullet"
    if stripped.startswith("<div") or stripped.startswith("</div"):
        return "html"
    return "body"


def _style_for_line(line: str, line_type: str) -> Tuple[str, int, str, int]:
    if line_type == "h1":
        return ("F2", 18, "0.07 0.16 0.36", 46)
    if line_type == "h2":
        return ("F2", 14, "0.10 0.32 0.74", 48)
    if line_type == "h3":
        return ("F2", 12, "0.15 0.23 0.35", 54)
    if line_type == "table":
        return ("F1", 9, "0.19 0.24 0.31", 52)
    if line_type == "bullet":
        return ("F1", 10, "0.17 0.19 0.24", 58)
    if line_type == "html":
        return ("F1", 10, "0.20 0.26 0.34", 52)
    return ("F1", 10, "0.17 0.19 0.24", 50)


def _wrap_pdf_line(line: str, line_type: str) -> List[str]:
    stripped = line.rstrip()
    if not stripped:
        return [line]
    width_map = {
        "h1": 46,
        "h2": 58,
        "h3": 66,
        "table": 82,
        "bullet": 78,
        "body": 82,
        "html": 82,
    }
    width = width_map.get(line_type, 82)
    wrapped = textwrap.wrap(
        stripped,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [stripped]


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    return " ".join(text.split())


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _find_chrome_executable() -> Optional[str]:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None
