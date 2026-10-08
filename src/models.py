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

    @field_validator("full_name", "email", "phone", "country_of_citizenship", "date_of_birth", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value or value.lower() in _PLACEHOLDERS:
                return None
        return value

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
