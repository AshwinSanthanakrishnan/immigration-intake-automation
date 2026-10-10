"""Runtime settings, loaded from .env and command-line flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_EFFORT = "medium"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Paths:
    inputs: Path
    form_dir: Path
    outputs: Path

    @property
    def followups(self) -> Path:
        return self.outputs / "followups"

    @property
    def screenshots(self) -> Path:
        return self.outputs / "screenshots"

    @property
    def checklists(self) -> Path:
        return self.outputs / "checklists"

    @property
    def needs_review(self) -> Path:
        return self.outputs / "needs_review"

    @property
    def logs(self) -> Path:
        return self.outputs / "logs"

    @property
    def report(self) -> Path:
        return self.outputs / "run_report.md"

    @property
    def report_pdf(self) -> Path:
        return self.outputs / "run_report.pdf"

    def artifact_dirs(self) -> tuple[Path, ...]:
        """Folders rewritten on every run (logs are kept)."""
        return (self.followups, self.screenshots, self.checklists, self.needs_review)


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    headless: bool
    slow_mo_ms: int
    offline: bool
    paths: Paths


def load_settings(
    *,
    headless: bool = False,
    slow_mo_ms: int = 300,
    offline: bool = False,
    inputs_dir: Path | None = None,
    model: str | None = None,
) -> Settings:
    """Read .env (without overriding variables already set) and combine it with CLI flags."""
    load_dotenv(PROJECT_ROOT / ".env")

    effort = os.getenv("CLAUDE_EFFORT", DEFAULT_EFFORT).strip().lower()
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"CLAUDE_EFFORT must be one of {', '.join(EFFORT_LEVELS)}; got {effort!r}")

    chosen_model = model or os.getenv("GEMINI_MODEL") or os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)

    return Settings(
        model=chosen_model.strip(),
        effort=effort,
        headless=headless,
        slow_mo_ms=0 if headless else max(slow_mo_ms, 0),
        offline=offline,
        paths=Paths(
            inputs=(inputs_dir or PROJECT_ROOT / "sample_inputs").resolve(),
            form_dir=PROJECT_ROOT / "intake_form",
            outputs=PROJECT_ROOT / "outputs",
        ),
    )


def has_api_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip())
