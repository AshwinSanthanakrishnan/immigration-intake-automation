"""Run the immigration intake automation end to end.

    python main.py               # headed browser with slow-mo, Gemini via GEMINI_API_KEY
    python main.py --headless    # no visible browser
    python main.py --offline     # rule-based stand-in instead of Gemini (no API key needed)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from src.config import has_api_key, load_settings
from src.logging_setup import setup_logging
from src.pipeline import run_pipeline
from src.reporting import format_duration

log = logging.getLogger("intake")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process client intake emails into the intake form.")
    parser.add_argument("--headless", action="store_true", help="run the browser without a window")
    parser.add_argument("--slow-mo", type=int, default=300, metavar="MS", help="delay between browser actions in headed mode (default 300)")
    parser.add_argument("--offline", action="store_true", help="use the rule-based stand-in instead of Gemini")
    parser.add_argument("--inputs", type=Path, metavar="DIR", help="folder of .txt intake emails (default sample_inputs/)")
    parser.add_argument("--model", type=str, default=None, help="Gemini model name (default: gemini-3.5-flash)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(
            headless=args.headless,
            slow_mo_ms=args.slow_mo,
            offline=args.offline,
            inputs_dir=args.inputs,
            model=args.model,
        )
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    log_file = setup_logging(settings.paths.logs)

    if settings.offline:
        from src.offline_ai import OfflineAI

        ai = OfflineAI()
        log.warning("OFFLINE mode: using a rule-based stand-in. No AI calls will be made.")
    else:
        if not has_api_key():
            log.error("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key, or run with --offline.")
            return 2

        if os.getenv("GEMINI_API_KEY"):
            from src.ai_client import GeminiAI

            ai = GeminiAI(model=settings.model)
        else:
            from src.ai_client import ClaudeAI

            ai = ClaudeAI(model=settings.model, effort=settings.effort)

    log.info("AI engine: %s", ai.name)
    try:
        output = run_pipeline(settings, ai)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2

    s = output.summary
    log.info(
        "Done in %s: %d processed, %d submitted (%d PASS, %d FAIL), %d follow-up, %d human review, %d error",
        format_duration(s.duration_s), s.processed, s.submitted, s.passed, s.failed, s.follow_ups, s.needs_review, s.errors,
    )
    log.info("Report: %s", output.pdf_path or output.report_path)
    if output.pdf_path:
        log.info("        %s (Markdown copy)", output.report_path)
    log.info("Log:    %s", log_file)
    return 1 if s.needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
