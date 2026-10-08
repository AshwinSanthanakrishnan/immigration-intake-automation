"""Unit tests for GeminiAI client integration and error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.ai_client import AIOutputError, GeminiAI
from src.models import CaseType, IntakeExtraction


def _make_dummy_response(text: str, in_tokens: int = 100, out_tokens: int = 50, finish_reason: str = "STOP"):
    candidate = MagicMock()
    candidate.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_token_count = in_tokens
    usage.candidates_token_count = out_tokens

    response = MagicMock()
    response.text = text
    response.candidates = [candidate]
    response.usage_metadata = usage
    return response


def test_gemini_ai_extract_success():
    mock_client = MagicMock()
    sample_payload = {
        "full_name": "Test Client",
        "email": "test@example.com",
        "phone": "(555) 010-0000",
        "country_of_citizenship": "Canada",
        "date_of_birth": "1990-01-31",
        "case_type": "H-1B",
        "urgency": {"level": "medium", "reason": "Active matter."},
        "missing_fields": [],
    }
    mock_client.models.generate_content.return_value = _make_dummy_response(json.dumps(sample_payload))

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    result_raw = ai.extract("I need help with my H-1B")

    parsed = IntakeExtraction.model_validate_json(result_raw)
    assert parsed.full_name == "Test Client"
    assert parsed.case_type == CaseType.H1B
    assert ai.usage_summary() == "1 Gemini API call(s), 100 input / 50 output tokens"


def test_gemini_ai_declined_error():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        text="", finish_reason="SAFETY"
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    with pytest.raises(AIOutputError, match="Gemini declined"):
        ai.extract("Some sensitive text")


def test_gemini_ai_empty_answer_error():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        text="", finish_reason="STOP"
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    with pytest.raises(AIOutputError, match="empty answer"):
        ai.extract("Some text")


def test_gemini_ai_draft_followup():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        "Subject: Missing Information\n\nPlease send your phone number."
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    record = IntakeExtraction(
        full_name="Kwame Boateng",
        email="kwame@example.com",
        phone=None,
        country_of_citizenship="Ghana",
        date_of_birth=None,
        case_type=CaseType.NATURALIZATION,
        urgency={"level": "low", "reason": "No rush"},
        missing_fields=["phone", "date_of_birth"],
    )

    draft = ai.draft_followup("email text", record)
    assert "Missing Information" in draft
    assert ai.usage_summary() == "1 Gemini API call(s), 100 input / 50 output tokens"


def test_gemini_ai_draft_checklist():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        "## Identity\n\n- [ ] Passport"
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    checklist = ai.draft_checklist(CaseType.H1B, "India")
    assert "## Identity" in checklist
