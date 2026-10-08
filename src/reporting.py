"""Step 7: summarize a run as Markdown (outputs/run_report.md)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import Outcome, RecordResult


@dataclass(frozen=True)
class RunSummary:
    processed: int
    submitted: int
    passed: int
    failed: int
    follow_ups: int
    needs_review: int
    errors: int
    duration_s: float

    @property
    def needs_attention(self) -> bool:
        """True when something went wrong technically (not just a normal business outcome)."""
        return self.failed > 0 or self.errors > 0


@dataclass(frozen=True)
class RunInfo:
    started_at: datetime
    ai_engine: str
    browser_mode: str
    ai_usage: str | None = None


def summarize(results: Sequence[RecordResult], duration_s: float) -> RunSummary:
    outcomes = Counter(result.outcome for result in results)
    submitted = [result for result in results if result.outcome is Outcome.SUBMITTED]
    return RunSummary(
        processed=len(results),
        submitted=len(submitted),
        passed=sum(result.verified is True for result in submitted),
        failed=sum(result.verified is not True for result in submitted),
        follow_ups=outcomes[Outcome.FOLLOW_UP],
        needs_review=outcomes[Outcome.NEEDS_REVIEW],
        errors=outcomes[Outcome.ERROR],
        duration_s=duration_s,
    )


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} min {rest:.0f} s"


def verification_label(result: RecordResult) -> str:
    if result.outcome is not Outcome.SUBMITTED:
        return "n/a"
    return "PASS" if result.verified else "FAIL"


def render_report(results: Sequence[RecordResult], summary: RunSummary, info: RunInfo, report_dir: Path) -> str:
    lines = [
        "# Intake automation run report",
        "",
        "| | |",
        "|---|---|",
        f"| Run started | {info.started_at:%Y-%m-%d %H:%M:%S} |",
        f"| Duration | {format_duration(summary.duration_s)} |",
        f"| AI engine | {_cell(info.ai_engine)} |",
    ]
    if info.ai_usage:
        lines.append(f"| AI usage | {_cell(info.ai_usage)} |")
    lines += [
        f"| Browser | {_cell(info.browser_mode)} |",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Records processed | {summary.processed} |",
        f"| Submitted to intake form | {summary.submitted} |",
        f"| Verification PASS | {summary.passed} |",
        f"| Verification FAIL | {summary.failed} |",
        f"| Held for client follow-up (missing information) | {summary.follow_ups} |",
        f"| Flagged for human review (invalid AI output) | {summary.needs_review} |",
        f"| Errors | {summary.errors} |",
        "",
        _status_line(summary),
        "",
    ]

    if not results:
        lines += ["No intake emails were found.", ""]
        return "\n".join(lines)

    lines += [
        "## Records",
        "",
        "| # | Source email | Client | Case type | Urgency | Outcome | Case ID | Verification | Time |",
        "|---:|---|---|---|---|---|---|---|---:|",
    ]
    for number, result in enumerate(results, start=1):
        record = result.extraction
        lines.append(
            "| "
            + " | ".join(
                [
                    str(number),
                    _cell(result.source_file),
                    _cell(record.full_name if record and record.full_name else "-"),
                    _cell(record.case_type.value if record else "-"),
                    record.urgency.level.capitalize() if record else "-",
                    result.outcome.value,
                    result.case_id or "-",
                    verification_label(result),
                    format_duration(result.duration_s),
                ]
            )
            + " |"
        )

    lines += ["", "## Details", ""]
    for number, result in enumerate(results, start=1):
        lines += _record_details(number, result, report_dir)

    lines += _next_steps(summary)
    lines += [
        "---",
        "*Demo run on fictional data. Nothing was sent to any client or external system; every draft "
        "requires human review.*",
        "",
    ]
    return "\n".join(lines)


def write_report(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _status_line(summary: RunSummary) -> str:
    if summary.needs_attention:
        return f"**Attention needed:** {summary.failed} verification failure(s), {summary.errors} error(s)."
    if summary.submitted:
        return "**All submitted records verified.**"
    return "**No records were submitted.**"


def _record_details(number: int, result: RecordResult, report_dir: Path) -> list[str]:
    record = result.extraction
    outcome = result.outcome.value
    if result.outcome is Outcome.SUBMITTED:
        outcome += f" · verification **{verification_label(result)}**"
        if result.verification_detail:
            outcome += f": {result.verification_detail}"

    lines = [f"### {number}. {result.source_file}", "", f"- **Outcome:** {outcome}"]
    if record:
        lines += [
            f"- **Urgency:** {record.urgency.level.capitalize()} · {record.urgency.reason}",
            f"- **Missing fields:** {', '.join(record.missing_field_labels) or 'none'}",
        ]
    if result.extraction_attempts:
        lines.append(f"- **Extraction attempts:** {result.extraction_attempts}")
    if result.artifacts:
        links = ", ".join(f"[{path.name}]({_relative(path, report_dir)})" for path in result.artifacts)
        lines.append(f"- **Artifacts:** {links}")
    if result.error:
        lines.append(f"- **Error:** {result.error}")
    lines.append("")
    return lines


def _next_steps(summary: RunSummary) -> list[str]:
    steps = []
    if summary.follow_ups:
        steps.append(f"- Review and send {summary.follow_ups} follow-up draft(s) in `followups/`.")
    if summary.passed:
        steps.append(f"- Attorney review of {summary.passed} document checklist draft(s) in `checklists/`.")
    if summary.needs_review:
        steps.append(f"- Complete {summary.needs_review} intake(s) by hand; details in `needs_review/`.")
    if summary.needs_attention:
        steps.append("- Investigate failures and errors above; full detail is in the run log under `logs/`.")
    return ["## Next steps for staff", "", *steps, ""] if steps else []


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
