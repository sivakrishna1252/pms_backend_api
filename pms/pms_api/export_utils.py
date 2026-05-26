"""Shared CSV/PDF export helpers for PMS admin list exports."""

from __future__ import annotations

import csv
from datetime import date

from django.http import HttpResponse

EXPORT_FORMAT_CSV = "csv"
EXPORT_FORMAT_EXCEL = "excel"
EXPORT_FORMAT_PDF = "pdf"
EXPORT_ROW_LIMIT = 5000


def format_display_date(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d %b %Y")


def write_csv_response(filename, column_keys, column_labels, rows, *, excel_compatible=False):
    content_type = "text/csv"
    if excel_compatible:
        content_type = "application/vnd.ms-excel"

    response = HttpResponse(content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(column_labels)
    for row in rows:
        writer.writerow([row.get(key, "") for key in column_keys])
    return response


def _escape_pdf_text(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf_response(filename, title, column_keys, column_labels, rows):
    lines = [title, "", " | ".join(column_labels)]
    lines.extend(
        " | ".join(str(row.get(key, "")) for key in column_keys)
        for row in rows[:120]
    )
    if len(rows) > 120:
        lines.append(f"... and {len(rows) - 120} more rows. Download CSV/Excel for full data.")

    content_lines = ["BT", "/F1 9 Tf", "40 780 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -14 Td")
        content_lines.append(f"({_escape_pdf_text(line[:115])}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )

    response = HttpResponse(bytes(pdf), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_file_response(export_format, filename_base, title, column_keys, column_labels, rows):
    export_rows = rows[:EXPORT_ROW_LIMIT]
    if export_format in {EXPORT_FORMAT_CSV, EXPORT_FORMAT_EXCEL}:
        extension = "csv" if export_format == EXPORT_FORMAT_CSV else "xls"
        return write_csv_response(
            f"{filename_base}.{extension}",
            column_keys,
            column_labels,
            export_rows,
            excel_compatible=export_format == EXPORT_FORMAT_EXCEL,
        )
    if export_format == EXPORT_FORMAT_PDF:
        return write_pdf_response(
            f"{filename_base}.pdf",
            title,
            column_keys,
            column_labels,
            export_rows,
        )
    return None


def matches_progress_band(percent, band: str) -> bool:
    if not band:
        return True
    value = percent if percent is not None else 0
    if band == "0-50":
        return value <= 50
    if band == "51-75":
        return 50 < value <= 75
    if band == "76-100":
        return value > 75
    return True


def project_status_label(status: str) -> str:
    mapping = {
        "PLANNED": "Not Started",
        "ACTIVE": "In Progress",
        "COMPLETED": "Completed",
        "DELAYED": "Delayed",
        "ARCHIVED": "Completed",
    }
    return mapping.get(status, status.replace("_", " ").title())


def milestone_status_label(status: str) -> str:
    mapping = {
        "NOT_STARTED": "Not Started",
        "IN_PROGRESS": "In Progress",
        "COMPLETED": "Completed",
        "DELAYED": "Delayed",
    }
    return mapping.get(status, status.replace("_", " ").title())


def task_status_label(status: str) -> str:
    mapping = {
        "NOT_STARTED": "Not Started",
        "IN_PROGRESS": "In Progress",
        "PAUSED": "Paused",
        "COMPLETED": "Completed",
        "DELAYED": "Delayed",
        "BLOCKED": "Stopped",
    }
    return mapping.get(status, status.replace("_", " ").title())


def ui_project_status_to_api(status: str) -> str | None:
    mapping = {
        "Not Started": "PLANNED",
        "In Progress": "ACTIVE",
        "Completed": "COMPLETED",
        "Delayed": "DELAYED",
    }
    return mapping.get(status)


def ui_milestone_status_to_api(status: str) -> str | None:
    mapping = {
        "Not Started": "NOT_STARTED",
        "In Progress": "IN_PROGRESS",
        "Completed": "COMPLETED",
        "Delayed": "DELAYED",
    }
    return mapping.get(status)
