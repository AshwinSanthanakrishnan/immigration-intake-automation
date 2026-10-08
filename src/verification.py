"""Step 5: confirm a submission actually landed, in the browser and in the intake system.

PASS requires all of: the confirmation panel is shown, the case ID has the expected format, and
the intake system holds a record under that case ID with the values that were submitted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from .models import IntakeExtraction

log = logging.getLogger(__name__)

CASE_ID_RE = re.compile(r"^IMM-\d{8}-[0-9A-F]{4}$")
CONFIRMATION_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    case_id: str | None
    detail: str
    screenshot: Path | None


def verify_submission(
    page: Page,
    record: IntakeExtraction,
    lookup_submission: Callable[[str], dict[str, Any] | None],
    screenshot_dir: Path,
    label: str,
) -> VerificationResult:
    case_id: str | None = None
    try:
        confirmation = page.locator("#confirmation")
        expect(confirmation).to_be_visible(timeout=CONFIRMATION_TIMEOUT_MS)
        expect(confirmation).to_contain_text("Intake received")

        case_id = page.locator("#case-id").inner_text().strip()
        if not CASE_ID_RE.match(case_id):
            raise AssertionError(f"case ID {case_id!r} does not match the expected format")

        stored = lookup_submission(case_id)
        if stored is None:
            raise AssertionError(f"intake system has no record of case {case_id}")
        mismatched = [name for name, value in _submitted_values(record).items() if stored.get(name) != value]
        if mismatched:
            raise AssertionError(f"intake system recorded different values for: {', '.join(mismatched)}")

        passed, detail = True, f"Confirmation shown; case {case_id} recorded with matching details"
    except (AssertionError, PlaywrightError) as exc:
        passed, detail = False, _failure_detail(page, exc)

    screenshot = _take_screenshot(page, screenshot_dir, case_id if passed and case_id else f"{label}_FAIL")
    log.log(logging.INFO if passed else logging.ERROR, "Verification %s: %s", "PASS" if passed else "FAIL", detail)
    return VerificationResult(passed=passed, case_id=case_id if passed else None, detail=detail, screenshot=screenshot)


def _submitted_values(record: IntakeExtraction) -> dict[str, str | None]:
    return {
        "full_name": record.full_name,
        "email": record.email,
        "phone": record.phone,
        "country_of_citizenship": record.country_of_citizenship,
        "date_of_birth": record.date_of_birth.isoformat() if record.date_of_birth else None,
        "case_type": record.case_type.value,
        "urgency": record.urgency.level,
    }


def _failure_detail(page: Page, exc: Exception) -> str:
    detail = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    try:
        error_box = page.locator("#form-error")
        if error_box.is_visible():
            detail += f" (form error: {error_box.inner_text().strip()})"
    except PlaywrightError:
        pass
    return detail


def _take_screenshot(page: Page, folder: Path, name: str) -> Path | None:
    path = folder / f"{name}.png"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return path
    except PlaywrightError as exc:
        log.warning("Could not save screenshot %s: %s", path.name, exc)
        return None
