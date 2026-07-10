from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from html import escape
from typing import Optional

from models import INBOX_LIST_LABELS, LeadRecord, SOURCE_LABELS

_INBOX_EXPORT_LABELS: dict[Optional[str], str] = {
    None: "📬 Новые",
    "all": "📋 Все qualified",
    **INBOX_LIST_LABELS,
}


def inbox_export_label(inbox_key: Optional[str]) -> str:
    if inbox_key == "all":
        return _INBOX_EXPORT_LABELS["all"]
    return _INBOX_EXPORT_LABELS.get(inbox_key, inbox_key or "📬 Новые")


def _source_label(record: LeadRecord) -> str:
    return SOURCE_LABELS.get(record.source.value, record.source.value)


def _fmt_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def build_csv_bytes(leads: list[LeadRecord], *, list_name: str) -> bytes:
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel UTF-8 BOM
    writer = csv.writer(buf)
    writer.writerow(
        [
            "#",
            "List",
            "Source",
            "Summary",
            "Reason",
            "Contact",
            "Author",
            "Date",
            "Text",
        ]
    )
    for i, lead in enumerate(leads, start=1):
        writer.writerow(
            [
                i,
                list_name,
                _source_label(lead),
                (lead.summary or "")[:500],
                (lead.reason or "")[:500],
                lead.contact or "",
                lead.author or "",
                _fmt_ts(lead.timestamp),
                lead.text[:4000],
            ]
        )
    return buf.getvalue().encode("utf-8")


def _lead_html_block(index: int, lead: LeadRecord) -> str:
    summary = escape(lead.summary or "—")
    reason = escape(lead.reason or "—")
    contact = escape(lead.contact or "—")
    text = escape(lead.text[:2000])
    source = escape(_source_label(lead))
    return f"""
    <div class="lead">
      <h3>{index}. [{source}] {summary}</h3>
      <p><b>Дата:</b> {_fmt_ts(lead.timestamp)}</p>
      <p><b>Контакт:</b> {contact}</p>
      <p><b>Автор:</b> {escape(lead.author or "—")}</p>
      <p><b>ИИ — почему подходит:</b> <i>{reason}</i></p>
      <pre class="text">{text}</pre>
    </div>
    """


def build_html_bytes(leads: list[LeadRecord], *, list_name: str) -> bytes:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks = "".join(_lead_html_block(i, lead) for i, lead in enumerate(leads, start=1))
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <title>WebDev Scout — {escape(list_name)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #111; }}
    h1 {{ font-size: 1.4rem; }}
    .meta {{ color: #555; margin-bottom: 24px; }}
    .lead {{ border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; page-break-inside: avoid; }}
    .lead h3 {{ margin: 0 0 8px; font-size: 1rem; }}
    pre.text {{ white-space: pre-wrap; background: #f6f6f6; padding: 10px; border-radius: 6px; font-size: 0.85rem; }}
    @media print {{ body {{ margin: 12px; }} .lead {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <h1>WebDev Scout — {escape(list_name)}</h1>
  <p class="meta">Сгенерировано: {generated} · Лидов: {len(leads)}</p>
  {blocks if blocks else "<p><i>Пусто</i></p>"}
</body>
</html>"""
    return html.encode("utf-8")


def build_pdf_bytes(leads: list[LeadRecord], *, list_name: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"WebDev Scout - {list_name}", ln=True)
    pdf.set_font("Helvetica", size=9)
    pdf.cell(
        0,
        6,
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | Leads: {len(leads)}",
        ln=True,
    )
    pdf.ln(4)
    width = pdf.w - pdf.l_margin - pdf.r_margin

    for i, lead in enumerate(leads, start=1):
        pdf.set_font("Helvetica", "B", size=10)
        title = f"{i}. [{_source_label(lead)}] {(lead.summary or '-')[:80]}"
        pdf.multi_cell(width, 5, _pdf_safe(title))
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(width, 4, _pdf_safe(f"Contact: {lead.contact or '-'}"))
        pdf.multi_cell(width, 4, _pdf_safe(f"Reason: {lead.reason or '-'}"))
        pdf.multi_cell(width, 4, _pdf_safe((lead.text or "")[:600]))
        pdf.ln(3)

    out = pdf.output()
    if isinstance(out, bytearray):
        return bytes(out)
    if isinstance(out, bytes):
        return out
    return str(out).encode("latin-1")


def _pdf_safe(text: str) -> str:
    """Helvetica is Latin-1 — keep readable ASCII fallback for Cyrillic."""
    return text.encode("latin-1", errors="replace").decode("latin-1")
