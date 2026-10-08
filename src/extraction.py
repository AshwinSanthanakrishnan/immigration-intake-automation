"""Step 2: turn an intake email into a validated IntakeExtraction.

The model's output is never trusted as-is: it must parse and validate against the Pydantic
model. An invalid answer is retried once with the validation error fed back; if the retry also
fails, the record is flagged for a human instead of guessing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .ai_client import AIOutputError, IntakeAI
from .models import IntakeExtraction

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first try plus one retry
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass
class ExtractionResult:
    record: IntakeExtraction | None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    raw_outputs: list[str] = field(default_factory=list)

    @property
    def needs_human_review(self) -> bool:
        return self.record is None


def parse_extraction(raw: str) -> IntakeExtraction:
    """Validate raw model output. Raises pydantic.ValidationError if it is not acceptable."""
    text = raw.strip()
    if fenced := _CODE_FENCE_RE.match(text):
        text = fenced.group(1)
    return IntakeExtraction.model_validate_json(text)


def describe_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def extract_intake(email_text: str, ai: IntakeAI, max_attempts: int = MAX_ATTEMPTS) -> ExtractionResult:
    result = ExtractionResult(record=None)
    validation_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        try:
            raw = ai.extract(email_text, previous_error=validation_error)
        except AIOutputError as exc:
            result.errors.append(f"attempt {attempt}: {exc}")
            log.warning("Extraction attempt %d/%d returned no usable output: %s", attempt, max_attempts, exc)
            continue

        result.raw_outputs.append(raw)
        try:
            result.record = parse_extraction(raw)
            return result
        except ValidationError as exc:
            validation_error = describe_validation_error(exc)
            result.errors.append(f"attempt {attempt}: {validation_error}")
            log.warning("Extraction attempt %d/%d failed validation: %s", attempt, max_attempts, validation_error)

    log.warning("Extraction failed after %d attempt(s); flagging for human review", max_attempts)
    return result
