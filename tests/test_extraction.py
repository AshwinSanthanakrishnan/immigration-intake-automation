"""Extraction validation and the retry-once-then-flag policy."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from src.ai_client import AIOutputError
from src.extraction import extract_intake, parse_extraction
from src.models import CaseType


def make_payload(**overrides):
    payload = {
        "full_name": "Test Client",
        "email": "test.client@example.com",
        "phone": "(555) 010-0000",
        "country_of_citizenship": "Canada",
        "date_of_birth": "1990-01-31",
        "case_type": "H-1B",
        "urgency": {"level": "medium", "reason": "Wants to start in the next few months."},
        "missing_fields": [],
    }
    payload.update(overrides)
    return payload


class ScriptedAI:
    """Returns scripted extraction outputs in order and records the feedback it was given."""

    name = "scripted test AI"

    def __init__(self, *outputs):
        self._outputs = list(outputs)
        self.previous_errors: list[str | None] = []

    def extract(self, email_text, previous_error=None):
        self.previous_errors.append(previous_error)
        output = self._outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, str) else json.dumps(output)


# --- validation -------------------------------------------------------------------------


def test_valid_payload_parses_into_a_complete_record():
    record = parse_extraction(json.dumps(make_payload()))

    assert record.is_complete
    assert record.case_type is CaseType.H1B
    assert record.date_of_birth == date(1990, 1, 31)
    assert record.urgency.level == "medium"


def test_empty_field_is_marked_missing_even_if_the_model_says_complete():
    record = parse_extraction(json.dumps(make_payload(date_of_birth=None, missing_fields=[])))

    assert record.missing_fields == ["date_of_birth"]
    assert not record.is_complete
    assert record.missing_field_labels == ["date of birth"]


def test_blank_and_placeholder_strings_count_as_missing():
    record = parse_extraction(json.dumps(make_payload(phone="   ", country_of_citizenship="N/A")))

    assert record.phone is None
    assert record.country_of_citizenship is None
    assert record.missing_fields == ["phone", "country_of_citizenship"]


def test_fields_the_model_flagged_stay_flagged():
    record = parse_extraction(json.dumps(make_payload(missing_fields=["phone"])))

    assert record.missing_fields == ["phone"]
    assert not record.is_complete


def test_missing_fields_are_deduplicated_in_a_stable_order():
    payload = make_payload(email=None, full_name=None, missing_fields=["email", "email", "full_name"])
    record = parse_extraction(json.dumps(payload))

    assert record.missing_fields == ["full_name", "email"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"case_type": "Asylum"}, id="unknown case type"),
        pytest.param({"urgency": {"level": "critical", "reason": "x"}}, id="unknown urgency level"),
        pytest.param({"urgency": {"level": "high", "reason": "  "}}, id="blank urgency reason"),
        pytest.param({"urgency": "high"}, id="urgency without reason"),
        pytest.param({"email": "not-an-email"}, id="malformed email"),
        pytest.param({"phone": "12"}, id="too few phone digits"),
        pytest.param({"date_of_birth": "14/03/1994"}, id="non-ISO date"),
        pytest.param({"date_of_birth": (date.today() + timedelta(days=1)).isoformat()}, id="future date of birth"),
        pytest.param({"missing_fields": ["passport_number"]}, id="unknown missing field"),
        pytest.param({"notes": "extra"}, id="unexpected extra field"),
        pytest.param({"marital_status": "engaged"}, id="unknown marital status"),
        pytest.param({"preferred_contact_method": "fax"}, id="unknown contact method"),
        pytest.param({"status_expires_on": "1800-01-01"}, id="implausible status expiry"),
    ],
)
def test_invalid_values_are_rejected(overrides):
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(make_payload(**overrides)))


def test_optional_details_are_normalized_and_never_required():
    payload = make_payload(
        marital_status="Married",
        preferred_contact_method="EMAIL",
        status_expires_on="2026-12-15",
        employer="N/A",
        consultation_requested=True,
    )
    record = parse_extraction(json.dumps(payload))

    assert record.is_complete
    assert record.marital_status == "married"
    assert record.preferred_contact_method == "email"
    assert record.status_expires_on == date(2026, 12, 15)
    assert record.employer is None
    assert record.optional_value("consultation_requested") == "Yes"


def test_optional_details_default_to_none_when_omitted():
    record = parse_extraction(json.dumps(make_payload()))

    assert record.occupation is None
    assert record.is_complete


def test_missing_key_is_rejected():
    payload = make_payload()
    del payload["case_type"]

    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(payload))


def test_non_json_is_rejected():
    with pytest.raises(ValidationError):
        parse_extraction("Sure! Here is the data you asked for.")


def test_json_in_a_code_fence_is_accepted():
    record = parse_extraction("```json\n" + json.dumps(make_payload()) + "\n```")

    assert record.full_name == "Test Client"


# --- retry policy -----------------------------------------------------------------------


def test_valid_first_answer_needs_one_attempt():
    ai = ScriptedAI(make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 1
    assert ai.previous_errors == [None]


def test_invalid_answer_is_retried_once_with_the_validation_error():
    ai = ScriptedAI(make_payload(case_type="Asylum"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 2
    assert ai.previous_errors[0] is None
    assert "case_type" in ai.previous_errors[1]


def test_two_invalid_answers_flag_the_record_for_human_review():
    ai = ScriptedAI("not json", make_payload(email="nope"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is None
    assert result.needs_human_review
    assert result.attempts == 2
    assert len(ai.previous_errors) == 2, "must not call the model a third time"
    assert len(result.errors) == 2
    assert result.raw_outputs == ["not json", json.dumps(make_payload(email="nope"))]


def test_refusal_counts_as_an_invalid_attempt():
    ai = ScriptedAI(AIOutputError("Claude declined the request"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 2
    assert ai.previous_errors == [None, None], "a refusal is not validation feedback"


def test_repeated_refusal_is_flagged():
    ai = ScriptedAI(AIOutputError("declined"), AIOutputError("declined"))

    result = extract_intake("email", ai)

    assert result.needs_human_review
    assert result.raw_outputs == []
