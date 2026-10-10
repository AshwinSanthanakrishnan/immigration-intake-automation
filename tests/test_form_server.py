"""The local intake-system stand-in: serves the form, validates submissions, issues case IDs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from src.config import PROJECT_ROOT
from src.form_server import IntakeServer, validate_submission
from src.verification import CASE_ID_RE

VALID = {
    "full_name": "Test Client",
    "email": "client@example.com",
    "phone": "555-010-0000",
    "country_of_citizenship": "Canada",
    "date_of_birth": "1990-01-31",
    "case_type": "Naturalization",
    "urgency": "low",
    "notes": "",
}


@pytest.fixture(scope="module")
def server():
    with IntakeServer(PROJECT_ROOT / "intake_form") as running:
        yield running


def post(server, payload):
    request = urllib.request.Request(
        server.url + "api/intake",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_serves_the_intake_form(server):
    with urllib.request.urlopen(server.url, timeout=5) as response:
        assert "New client intake" in response.read().decode()


def test_valid_submission_gets_a_case_id_and_is_stored(server):
    status, body = post(server, VALID)

    assert status == 201
    assert CASE_ID_RE.match(body["case_id"])
    assert server.get_submission(body["case_id"])["email"] == VALID["email"]


def test_incomplete_submission_is_rejected(server):
    status, body = post(server, {**VALID, "date_of_birth": ""})

    assert status == 422
    assert "date_of_birth is required" in body["problems"]


def test_validate_submission_checks_allowed_values():
    problems = validate_submission({**VALID, "case_type": "Asylum", "urgency": "urgent", "date_of_birth": "31/01/1990"})

    assert len(problems) == 3


def test_validate_submission_checks_optional_choices_and_dates():
    problems = validate_submission(
        {**VALID, "marital_status": "engaged", "preferred_contact_method": "fax", "status_expires_on": "12/15/2026"}
    )

    assert len(problems) == 3


def test_blank_optional_fields_are_accepted():
    blanks = {"marital_status": "", "preferred_contact_method": "", "status_expires_on": "", "occupation": ""}

    assert validate_submission({**VALID, **blanks}) == []
