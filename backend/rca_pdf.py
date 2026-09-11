"""
backend/rca_pdf.py

Renders an RCAReport as a downloadable PDF using reportlab.
"""
from __future__ import annotations

import io
import time

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from agents.schemas import RCAReport


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def render_rca_pdf(rca: RCAReport) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RCATitle", parent=styles["Title"], textColor=colors.HexColor("#0a0e1a"))
    h2 = ParagraphStyle("RCAH2", parent=styles["Heading2"], textColor=colors.HexColor("#003b4d"), spaceBefore=14)
    body = styles["BodyText"]

    story = []
    story.append(Paragraph(rca.title, title_style))
    story.append(Paragraph(f"Incident ID: {rca.incident_id} &nbsp;&nbsp;|&nbsp;&nbsp; Severity: {rca.severity.value}", body))
    story.append(Paragraph(f"Generated: {_fmt_time(rca.generated_at)}", body))
    story.append(Spacer(1, 16))

    story.append(Paragraph("Root Cause", h2))
    story.append(Paragraph(rca.root_cause, body))

    story.append(Paragraph("Affected Services", h2))
    story.append(ListFlowable([ListItem(Paragraph(s, body)) for s in rca.affected_services], bulletType="bullet"))

    story.append(Paragraph("Timeline", h2))
    table_data = [["Timestamp", "Actor", "Event"]]
    for event in rca.timeline:
        table_data.append([_fmt_time(event.timestamp), event.actor, Paragraph(event.event, body)])
    table = Table(table_data, colWidths=[1.4 * inch, 1.3 * inch, 3.8 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a0e1a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f7")]),
            ]
        )
    )
    story.append(table)

    story.append(Paragraph("Contributing Factors", h2))
    if rca.contributing_factors:
        story.append(ListFlowable([ListItem(Paragraph(f, body)) for f in rca.contributing_factors], bulletType="bullet"))
    else:
        story.append(Paragraph("None identified.", body))

    story.append(Paragraph("Remediation Taken", h2))
    if rca.remediation_taken:
        story.append(ListFlowable([ListItem(Paragraph(r, body)) for r in rca.remediation_taken], bulletType="bullet"))
    else:
        story.append(Paragraph("None recorded.", body))

    story.append(Paragraph("Prevention Recommendations", h2))
    story.append(ListFlowable([ListItem(Paragraph(p, body)) for p in rca.prevention_recommendations], bulletType="bullet"))

    story.append(Paragraph("Lessons Learned (Blameless)", h2))
    story.append(Paragraph(rca.lessons_learned, body))

    doc.build(story)
    return buf.getvalue()
