from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


def write_simple_pdf(lines: Iterable[str], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    escaped_pages = _paginate(list(lines), max_lines=42)
    font_object = "1 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    objects = [font_object]
    page_object_numbers = []
    content_object_numbers = []
    next_object_number = 2

    for page_lines in escaped_pages:
        content_stream = _build_content_stream(page_lines)
        content_object_numbers.append(next_object_number)
        objects.append(
            "%d 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n"
            % (next_object_number, len(content_stream.encode("utf-8")), content_stream)
        )
        next_object_number += 1

        page_object_numbers.append(next_object_number)
        objects.append(
            "%d 0 obj\n<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 1 0 R >> >> /Contents %d 0 R >>\nendobj\n"
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


def _paginate(lines: List[str], max_lines: int) -> List[List[str]]:
    pages = []
    for index in range(0, len(lines), max_lines):
        pages.append(lines[index : index + max_lines])
    return pages or [[]]


def _build_content_stream(lines: List[str]) -> str:
    y_position = 760
    stream_lines = ["BT", "/F1 11 Tf"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_lines.append("1 0 0 1 50 %d Tm (%s) Tj" % (y_position, escaped[:110]))
        y_position -= 16
    stream_lines.append("ET")
    return "\n".join(stream_lines)
