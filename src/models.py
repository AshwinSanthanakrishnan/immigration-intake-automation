"""Data models.

`IntakeExtraction` is the contract between Claude and the rest of the pipeline: Claude's JSON
must validate against it before anything downstream (form filling, follow-ups) happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaseType(str, Enum):
    H1B = "H-1B"
    FAMILY_GREEN_CARD = "Family Green Card"
    NATURALIZATION = "Naturalization"
    OTHER = "Other"


# Fields that must be present before a record may be submitted, with the client-friendly
# wording used in follow-up emails.
REQUIRED_FIELDS: dict[str, str] = {
    "full_name": "full legal name",
    "email": "email address",
    "phone": "phone number",
    "country_of_citizenship": "country of citizenship",
    "date_of_birth": "date of birth",
}
RequiredField = Literal["full_name", "email", "phone", "country_of_citizenship", "date_of_birth"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDERS = {"null", "none", "n/a", "na", "unknown", "not provided", "not stated"}
_EARLIEST_DOB = date(1900, 1, 1)
_LATEST_EXPIRY_YEARS = 30

MaritalStatus = Literal["single", "married", "divorced", "widowed", "separated"]
ContactMethod = Literal["email", "phone"]

# Optional client details: captured when the email states them, never required for submission.
# Order and labels are shared by the intake form, the verification step, and the run report.
OPTIONAL_FIELDS: dict[str, str] = {
    "country_of_birth": "Country of birth",
    "current_location": "Current city / state",
    "marital_status": "Marital status",
    "preferred_contact_method": "Preferred contact method",
    "current_immigration_status": "Current immigration status",
    "status_expires_on": "Status expires on",
    "occupation": "Occupation",
    "employer": "Employer",
    "highest_education": "Highest education",
    "case_subtype": "Case subtype",
    "consultation_requested": "Consultation requested",
    "matter_summary": "Matter summary",
}
_OPTIONAL_TEXT_FIELDS = (
    "country_of_birth",
    "current_location",
    "current_immigration_status",
    "occupation",
    "employer",
    "highest_education",
    "case_subtype",
    "matter_summary",
)


class Urgency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["low", "medium", "high"]
    reason: str = Field(description="One short sentence explaining the level, citing the email.")

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("urgency reason must not be empty")
        return value


class IntakeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(description="The prospective client's full name; null if not stated.")
    email: str | None = Field(description="The client's email address; null if not stated.")
    phone: str | None = Field(description="The client's phone number as written; null if not stated.")
    country_of_citizenship: str | None = Field(
        description="Country the client says they are a citizen or national of; null if not stated."
    )
    date_of_birth: date | None = Field(description="YYYY-MM-DD; null unless the full date is stated.")
    case_type: CaseType
    urgency: Urgency
    missing_fields: list[RequiredField] = Field(description="Required fields returned as null.")

    # Optional details (see OPTIONAL_FIELDS). Null whenever the email does not state them.
    country_of_birth: str | None = Field(default=None, description="Country the client was born in; null if not stated.")
    current_location: str | None = Field(
        default=None, description="City and state/country where the client currently lives; null if not stated."
    )
    marital_status: MaritalStatus | None = Field(default=None, description="Null unless stated or clearly implied.")
    preferred_contact_method: ContactMethod | None = Field(
        default=None, description="How the client asks to be contacted; null if not stated."
    )
    current_immigration_status: str | None = Field(
        default=None, description="Current U.S. status as stated, e.g. 'F-1 (STEM OPT)', 'H-1B', 'Permanent resident'."
    )
    status_expires_on: date | None = Field(
        default=None, description="YYYY-MM-DD expiry of the current status or work authorization; null if not stated."
    )
    occupation: str | None = Field(default=None, description="Job title or profession; null if not stated.")
    employer: str | None = Field(default=None, description="Current or sponsoring employer's name; null if not stated.")
    highest_education: str | None = Field(
        default=None, description="Highest degree and field, e.g. 'M.S. Computer Science'; null if not stated."
    )
    case_subtype: str | None = Field(
        default=None, description="More specific matter, e.g. 'H-1B transfer', 'Adjustment of status'; null if unclear."
    )
    consultation_requested: bool | None = Field(
        default=None, description="True if the client asks for a consultation or call; null if not mentioned."
    )
    matter_summary: str | None = Field(
        default=None, description="One or two neutral sentences summarizing what the client is asking for."
    )

    @field_validator(
        "full_name", "email", "phone", "country_of_citizenship", "date_of_birth", *_OPTIONAL_TEXT_FIELDS,
        "marital_status", "preferred_contact_method", "status_expires_on", "consultation_requested",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value or value.lower() in _PLACEHOLDERS:
                return None
        return value

    @field_validator("marital_status", "preferred_contact_method", mode="before")
    @classmethod
    def _lowercase_choice(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError(f"not a valid email address: {value!r}")
        return value

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        if value is not None and not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError(f"phone number should have 7-15 digits: {value!r}")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def _plausible_dob(cls, value: date | None) -> date | None:
        if value is not None and not _EARLIEST_DOB <= value <= date.today():
            raise ValueError(f"not a plausible date of birth: {value.isoformat()}")
        return value

    @field_validator("status_expires_on")
    @classmethod
    def _plausible_expiry(cls, value: date | None) -> date | None:
        latest = date(date.today().year + _LATEST_EXPIRY_YEARS, 12, 31)
        if value is not None and not _EARLIEST_DOB <= value <= latest:
            raise ValueError(f"not a plausible status expiry date: {value.isoformat()}")
        return value

    @model_validator(mode="after")
    def _reconcile_missing_fields(self) -> IntakeExtraction:
        """Code, not the model, has the final say on what is missing.

        Any required field that is empty is added to missing_fields even if Claude said the
        record was complete; anything Claude flagged is kept, so staff confirm it with the client.
        """
        flagged = set(self.missing_fields)
        flagged |= {name for name in REQUIRED_FIELDS if getattr(self, name) is None}
        self.missing_fields = [name for name in REQUIRED_FIELDS if name in flagged]
        return self

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    @property
    def missing_field_labels(self) -> list[str]:
        return [REQUIRED_FIELDS[name] for name in self.missing_fields]

    def optional_value(self, name: str) -> str | None:
        """An optional field as display text (None when not stated)."""
        value = getattr(self, name)
        if value is None:
            return None
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, date):
            return value.isoformat()
        if name in ("marital_status", "preferred_contact_method"):
            return value.capitalize()
        return value

    def client_details(self) -> list[tuple[str, str, str | None]]:
        """Every client field as (field name, label, display value or None if not stated)."""
        rows = [
            ("full_name", "Full legal name", self.full_name),
            ("email", "Email", self.email),
            ("phone", "Phone", self.phone),
            ("country_of_citizenship", "Country of citizenship", self.country_of_citizenship),
            ("date_of_birth", "Date of birth", self.date_of_birth.isoformat() if self.date_of_birth else None),
            ("case_type", "Case type", self.case_type.value),
            ("urgency", "Urgency", self.urgency.level.capitalize()),
        ]
        rows += [(name, label, self.optional_value(name)) for name, label in OPTIONAL_FIELDS.items()]
        return rows


class Outcome(str, Enum):
    SUBMITTED = "Submitted"
    FOLLOW_UP = "Follow-up drafted"
    NEEDS_REVIEW = "Needs human review"
    ERROR = "Error"


@dataclass
class RecordResult:
    """What happened to one intake email during a run."""

    source_file: str
    outcome: Outcome = Outcome.ERROR
    extraction: IntakeExtraction | None = None
    extraction_attempts: int = 0
    case_id: str | None = None
    verified: bool | None = None  # None = not submitted, so nothing to verify
    verification_detail: str | None = None
    artifacts: list[Path] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0
