"""Orchestrates one run: every intake email goes read -> extract -> decide -> act -> verify.

Each record is processed inside its own error boundary, so one bad email, API failure, or
browser problem is recorded in the report and the run moves on to the next record.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ai_client import IntakeAI
from .config import Settings
from .drafts import ReviewTicket, save_checklist, save_followup, save_review_ticket
from .extraction import extract_intake
from .form_filler import BrowserSession, fill_intake_form
from .form_server import IntakeServer
from .input_reader import list_intake_files, read_intake_email
from .models import IntakeExtraction, Outcome, RecordResult
from .reporting import RunInfo, RunSummary, render_report, summarize, write_report
from .verification import verify_submission

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOutput:
    results: list[RecordResult]
    summary: RunSummary
    report_path: Path


def run_pipeline(settings: Settings, ai: IntakeAI) -> RunOutput:
    started_at = datetime.now()
    clock = time.perf_counter()
    paths = settings.paths
    _reset_artifact_dirs(paths.artifact_dirs())

    files = list_intake_files(paths.inputs)
    results: list[RecordResult] = []
    with IntakeServer(paths.form_dir) as server, BrowserSession(
        headless=settings.headless, slow_mo_ms=settings.slow_mo_ms
    ) as browser:
        for index, path in enumerate(files, start=1):
            log.info("--- [%d/%d] %s ---", index, len(files), path.name)
            results.append(_process_record(path, settings, ai, server, browser))

    summary = summarize(results, time.perf_counter() - clock)
    browser_mode = "headless" if settings.headless else f"headed, slow-mo {settings.slow_mo_ms} ms"
    info = RunInfo(started_at=started_at, ai_engine=ai.name, browser_mode=browser_mode, ai_usage=ai.usage_summary())
    report = render_report(results, summary, info, report_dir=paths.report.parent)
    report_path = write_report(paths.report, report)
    return RunOutput(results=results, summary=summary, report_path=report_path)


def _process_record(
    path: Path, settings: Settings, ai: IntakeAI, server: IntakeServer, browser: BrowserSession
) -> RecordResult:
    paths = settings.paths
    result = RecordResult(source_file=path.name)
    clock = time.perf_counter()
    stage = "read"
    try:
        email = read_intake_email(path)

        stage = "extract"
        extraction = extract_intake(email.text, ai)
        result.extraction_attempts = extraction.attempts
        if extraction.record is None:
            ticket = ReviewTicket(path.name, extraction.attempts, extraction.errors, extraction.raw_outputs)
            result.artifacts.append(save_review_ticket(paths.needs_review, ticket))
            result.outcome = Outcome.NEEDS_REVIEW
            result.error = "AI output failed validation twice: " + " | ".join(extraction.errors)
            return result

        record = result.extraction = extraction.record
        log.info(
            "Extracted: %s | %s | urgency %s | missing: %s",
            record.full_name or "(no name)",
            record.case_type.value,
            record.urgency.level,
            ", ".join(record.missing_fields) or "none",
        )

        if not record.is_complete:
            stage = "follow-up"
            draft = ai.draft_followup(email.text, record)
            result.artifacts.append(save_followup(paths.followups, path.name, record, draft, ai.name))
            result.outcome = Outcome.FOLLOW_UP
            log.info("Not submitted: drafted a follow-up asking for %s", ", ".join(record.missing_field_labels))
            return result

        stage = "form"
        page = browser.page()
        fill_intake_form(page, server.url, record, _form_notes(path.name, record))
        result.outcome = Outcome.SUBMITTED

        stage = "verify"
        verification = verify_submission(
            page, record, server.get_submission, paths.screenshots, label=Path(path.name).stem
        )
        result.verified = verification.passed
        result.verification_detail = verification.detail
        result.case_id = verification.case_id
        if verification.screenshot:
            result.artifacts.append(verification.screenshot)
        browser.pause_for_viewer()

        if verification.passed and verification.case_id:
            stage = "checklist"
            draft = ai.draft_checklist(record.case_type, record.country_of_citizenship)
            result.artifacts.append(save_checklist(paths.checklists, verification.case_id, record, draft, ai.name))
            log.info("Saved document checklist draft for case %s", verification.case_id)

    except Exception as exc:  # one bad record must not stop the run
        result.error = f"{stage}: {type(exc).__name__}: {exc}"
        log.error("Record %s failed during %s: %s", path.name, stage, exc)
        log.debug("Traceback for %s", path.name, exc_info=True)  # file log only
        if stage in ("form", "verify"):
            browser.reset_page()
        # A failure after submission (e.g. while drafting the checklist) keeps the
        # Submitted outcome; anything earlier means the record was not handled.
        if result.outcome is not Outcome.SUBMITTED:
            result.outcome = Outcome.ERROR
        elif result.verified is None:
            result.verified = False
            result.verification_detail = "verification did not complete"
    finally:
        result.duration_s = time.perf_counter() - clock
    return result


def _form_notes(source_file: str, record: IntakeExtraction) -> str:
    return (
        f"Urgency ({record.urgency.level}): {record.urgency.reason}\n"
        f"Auto-entered from intake email {source_file}. Pending attorney review."
    )


def _reset_artifact_dirs(folders: tuple[Path, ...]) -> None:
    """Start each run with empty artifact folders so the report matches what is on disk."""
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
