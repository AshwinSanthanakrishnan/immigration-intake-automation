"""The --offline stand-in should drive the sample emails down the intended demo paths."""

from __future__ import annotations

from src.config import PROJECT_ROOT
from src.extraction import extract_intake
from src.input_reader import read_intake_email
from src.models import CaseType
from src.offline_ai import OfflineAI

SAMPLES = PROJECT_ROOT / "sample_inputs"


def extract(filename):
    result = extract_intake(read_intake_email(SAMPLES / filename).text, OfflineAI())
    assert result.record is not None, result.errors
    return result.record


def test_h1b_sample_is_complete():
    record = extract("01_h1b_inquiry.txt")

    assert record.is_complete
    assert record.case_type is CaseType.H1B
    assert record.urgency.level == "high"


def test_family_sample_is_complete_and_reads_day_month_year():
    record = extract("02_family_green_card_inquiry.txt")

    assert record.is_complete
    assert record.case_type is CaseType.FAMILY_GREEN_CARD
    assert record.date_of_birth.isoformat() == "1996-07-22"


def test_messy_sample_is_missing_phone_and_date_of_birth():
    record = extract("03_naturalization_inquiry_messy.txt")

    assert record.case_type is CaseType.NATURALIZATION
    assert record.missing_fields == ["phone", "date_of_birth"]


def test_h1b_sample_captures_background_details():
    record = extract("01_h1b_inquiry.txt")

    assert record.current_location == "Austin, Texas"
    assert record.current_immigration_status == "F-1 (STEM OPT)"
    assert record.status_expires_on.isoformat() == "2026-12-15"
    assert record.employer == "Northwind Analytics LLC"
    assert record.marital_status == "single"
    assert record.preferred_contact_method == "email"
    assert record.consultation_requested is True


def test_labeled_case_type_and_urgency_win_over_keywords():
    carlos = extract("08_humanitarian_carlos_mendoza.pdf")
    kenji = extract("06_naturalization_kenji_sato.pdf")

    assert carlos.case_type is CaseType.OTHER, "'Citizenship: Colombia' must not mean naturalization"
    assert kenji.urgency.level == "medium", "'no emergency deadlines' must not mean high urgency"
