"""Step 7 (continued): render the run report as a styled PDF (outputs/run_report.pdf).

The report is built as HTML from the same results as the Markdown report, then printed to PDF
by headless Chromium, which Playwright already provides. Screenshots are embedded, so the PDF
can be shared on its own.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

from .models import Outcome, RecordResult
from .reporting import RunInfo, RunSummary, format_duration, next_steps, verification_label

log = logging.getLogger(__name__)

_OUTCOME_TONE = {
    Outcome.SUBMITTED: "ok",
    Outcome.FOLLOW_UP: "warn",
    Outcome.NEEDS_REVIEW: "warn",
    Outcome.ERROR: "bad",
}
_URGENCY_TONE = {"high": "bad", "medium": "warn", "low": "muted"}

_CSS = """
@page { size: Letter; margin: 16mm 14mm 18mm; }
* { box-sizing: border-box; }
body {
  margin: 0; color: #1b2430; font: 10pt/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 { font-size: 20pt; margin: 0; }
h2 { font-size: 13pt; margin: 22px 0 10px; color: #1d4a73; border-bottom: 1px solid #d8dee6; padding-bottom: 4px; }
.banner { background: #1d4a73; color: #fff; border-radius: 8px; padding: 16px 20px; }
.banner .sub { opacity: .85; margin-top: 2px; }
.meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px 16px; margin-top: 12px; font-size: 9pt; }
.meta div span { display: block; opacity: .75; font-size: 8pt; text-transform: uppercase; letter-spacing: .5px; }
.tiles { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin-top: 14px; }
.tile { border: 1px solid #d8dee6; border-radius: 6px; padding: 8px 10px; }
.tile b { display: block; font-size: 16pt; line-height: 1.1; }
.tile span { font-size: 8pt; color: #5c6777; }
.status { margin-top: 12px; padding: 8px 12px; border-radius: 6px; font-weight: 600; }
.status.ok { background: #e7f4ed; color: #1d7a4e; }
.status.bad { background: #fcecec; color: #a3262c; }
.status.muted { background: #f3f5f8; color: #5c6777; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
th { text-align: left; background: #f3f5f8; color: #5c6777; font-weight: 600; }
th, td { padding: 5px 6px; border-bottom: 1px solid #e5e9ef; vertical-align: top; }
td.num, th.num { text-align: right; }
tr { page-break-inside: avoid; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 8pt; font-weight: 600; white-space: nowrap; }
.badge.ok { background: #e7f4ed; color: #1d7a4e; }
.badge.warn { background: #fdf3e1; color: #8a5a00; }
.badge.bad { background: #fcecec; color: #a3262c; }
.badge.muted { background: #eef1f5; color: #5c6777; }
.record { border: 1px solid #d8dee6; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; page-break-inside: avoid; }
.record-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.record-head h3 { margin: 0; font-size: 11.5pt; }
.src { color: #5c6777; font-size: 8.5pt; }
.record-body { display: flex; gap: 14px; margin-top: 8px; }
.details { flex: 1; display: grid; grid-template-columns: max-content 1fr max-content 1fr; gap: 3px 10px; font-size: 8.5pt; }
.details dt { color: #5c6777; }
.details dd { margin: 0; }
.details .wide { grid-column: 2 / -1; }
.missing { color: #a3262c; font-weight: 600; }
.shot { width: 170px; flex: none; }
.shot img { width: 100%; border: 1px solid #d8dee6; border-radius: 4px; }
.shot figcaption { font-size: 7.5pt; color: #5c6777; text-align: center; }
.facts { margin: 8px 0 0; padding: 0; list-style: none; font-size: 8.5pt; color: #3b4656; }
.facts li { margin-top: 2px; }
.steps li { margin-bottom: 4px; }
.foot { margin-top: 20px; color: #5c6777; font-size: 8pt; border-top: 1px solid #d8dee6; padding-top: 8px; }
"""

_FOOTER = (
    '<div style="width:100%;font-size:7pt;color:#5c6777;padding:0 14mm;display:flex;justify-content:space-between;">'
    "<span>Intake automation run report · fictional demo data</span>"
    '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>'
)


def render_report_html(results: Sequence[RecordResult], summary: RunSummary, info: RunInfo) -> str:
    meta = [
        ("Run started", f"{info.started_at:%Y-%m-%d %H:%M:%S}"),
        ("Duration", format_duration(summary.duration_s)),
        ("AI engine", info.ai_engine),
        ("Browser", info.browser_mode),
    ]
    if info.ai_usage:
        meta.append(("AI usage", info.ai_usage))
    tiles = [
        ("Processed", summary.processed),
        ("Submitted", summary.submitted),
        ("Verified PASS", summary.passed),
        ("Verified FAIL", summary.failed),
        ("Follow-up", summary.follow_ups),
        ("Human review", summary.needs_review),
        ("Errors", summary.errors),
    ]
    if summary.needs_attention:
        status = ("bad", f"Attention needed: {summary.failed} verification failure(s), {summary.errors} error(s).")
    elif summary.submitted:
        status = ("ok", "All submitted records verified.")
    else:
        status = ("muted", "No records were submitted.")

    parts = [
        '<div class="banner"><h1>Intake automation run report</h1>',
        '<div class="sub">Example Immigration Law Group · Client intake</div><div class="meta">',
        *(f"<div><span>{escape(label)}</span>{escape(value)}</div>" for label, value in meta),
        '</div></div><div class="tiles">',
        *(f'<div class="tile"><b>{value}</b><span>{escape(label)}</span></div>' for label, value in tiles),
        f'</div><div class="status {status[0]}">{escape(status[1])}</div>',
    ]

    if not results:
        parts.append("<p>No intake emails were found.</p>")
    else:
        parts += ["<h2>Records</h2>", _records_table(results), "<h2>Client details</h2>"]
        parts += [_record_card(number, result) for number, result in enumerate(results, start=1)]

    if steps := next_steps(summary):
        parts += ["<h2>Next steps for staff</h2>", '<ul class="steps">']
        parts += [f"<li>{escape(step)}</li>" for step in steps]
        parts.append("</ul>")
    parts.append(
        '<div class="foot">Demo run on fictional data. Nothing was sent to any client or external system; '
        "every draft requires human review.</div>"
    )
    body = "\n".join(parts)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Intake run report</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def write_pdf_report(path: Path, html: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)  # PDF printing needs headless Chromium
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(
                path=str(path),
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=_FOOTER,
            )
        finally:
            browser.close()
    return path


def _records_table(results: Sequence[RecordResult]) -> str:
    rows = []
    for number, result in enumerate(results, start=1):
        record = result.extraction
        urgency = record.urgency.level if record else None
        cells = [
            f'<td class="num">{number}</td>',
            f"<td>{escape(record.full_name if record and record.full_name else '-')}</td>",
            f"<td>{escape(record.case_type.value if record else '-')}</td>",
            f"<td>{_badge(urgency.capitalize(), _URGENCY_TONE[urgency]) if urgency else '-'}</td>",
            f"<td>{_badge(result.outcome.value, _OUTCOME_TONE[result.outcome])}</td>",
            f"<td>{escape(result.case_id or '-')}</td>",
            f"<td>{_verification_badge(result)}</td>",
            f'<td class="num">{format_duration(result.duration_s)}</td>',
        ]
        rows.append(f"<tr>{''.join(cells)}</tr>")
    head = (
        '<tr><th class="num">#</th><th>Client</th><th>Case type</th><th>Urgency</th><th>Outcome</th>'
        '<th>Case ID</th><th>Verification</th><th class="num">Time</th></tr>'
    )
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _record_card(number: int, result: RecordResult) -> str:
    record = result.extraction
    name = record.full_name if record and record.full_name else "Unidentified client"
    badges = _badge(result.outcome.value, _OUTCOME_TONE[result.outcome])
    if result.outcome is Outcome.SUBMITTED:
        badges += " " + _verification_badge(result)

    details = ""
    if record:
        items = []
        for field, label, value in record.client_details():
            if field == "matter_summary":
                continue
            if field in record.missing_fields:
                shown = '<span class="missing">Missing</span>'
            else:
                shown = escape(value or "-")
            items.append(f"<dt>{escape(label)}</dt><dd>{shown}</dd>")
        if record.matter_summary:
            items.append(f'<dt>Matter summary</dt><dd class="wide">{escape(record.matter_summary)}</dd>')
        details = f'<dl class="details">{"".join(items)}</dl>'

    facts = []
    if record:
        facts.append(f"<b>Urgency reason:</b> {escape(record.urgency.reason)}")
    if result.case_id:
        facts.append(f"<b>Case ID:</b> {escape(result.case_id)}")
    if result.verification_detail:
        facts.append(f"<b>Verification:</b> {escape(result.verification_detail)}")
    if result.extraction_attempts:
        facts.append(f"<b>Extraction attempts:</b> {result.extraction_attempts}")
    files = [path.name for path in result.artifacts if path.suffix != ".png"]
    if files:
        facts.append(f"<b>Drafts:</b> {escape(', '.join(files))}")
    if result.error:
        facts.append(f'<b>Error:</b> <span class="missing">{escape(result.error)}</span>')
    facts_html = "".join(f"<li>{fact}</li>" for fact in facts)

    return (
        f'<section class="record"><div class="record-head"><h3>{number}. {escape(name)}</h3><span>{badges}</span></div>'
        f'<div class="src">Source: {escape(result.source_file)}</div>'
        f'<div class="record-body">{details}{_screenshot(result)}</div>'
        f'<ul class="facts">{facts_html}</ul></section>'
    )


def _screenshot(result: RecordResult) -> str:
    shot = next((path for path in result.artifacts if path.suffix == ".png"), None)
    if shot is None or not shot.is_file():
        return ""
    data = base64.b64encode(shot.read_bytes()).decode("ascii")
    return (
        f'<figure class="shot"><img src="data:image/png;base64,{data}" alt="Confirmation screenshot">'
        "<figcaption>Intake confirmation</figcaption></figure>"
    )


def _verification_badge(result: RecordResult) -> str:
    label = verification_label(result)
    return _badge(label, {"PASS": "ok", "FAIL": "bad"}.get(label, "muted"))


def _badge(text: str, tone: str) -> str:
    return f'<span class="badge {tone}">{escape(text)}</span>'
