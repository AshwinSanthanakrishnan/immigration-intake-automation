"""Step 1: read new-client intake emails and documents (.txt, .pdf, .eml, .md) from a folder."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

try:
    import pypdf
except ImportError:
    pypdf = None

log = logging.getLogger(__name__)

MAX_EMAIL_CHARS = 50_000
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".eml", ".md"}


class InputError(Exception):
    """An intake file that cannot be processed (unreadable, empty, or too large)."""


@dataclass(frozen=True)
class IntakeEmail:
    source_file: str
    text: str


def list_intake_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise FileNotFoundError(f"Input folder not found: {folder}")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    log.info("Found %d intake document/email(s) in %s", len(files), folder)
    return files


def read_intake_email(path: Path) -> IntakeEmail:
    suffix = path.suffix.lower()
    text = ""
    if suffix == ".pdf":
        if pypdf is None:
            raise InputError("pypdf is required to read PDF documents. Run `pip install pypdf`.")
        try:
            reader = pypdf.PdfReader(path)
            pages_text = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(t.strip() for t in pages_text if t.strip()).strip()
        except Exception as exc:
            raise InputError(f"could not parse PDF {path.name}: {exc}") from exc
    else:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise InputError(f"could not read {path.name}: {exc}") from exc

    if not text:
        raise InputError(f"{path.name} is empty or contains no extractable text")
    if len(text) > MAX_EMAIL_CHARS:
        raise InputError(f"{path.name} is {len(text):,} characters (limit {MAX_EMAIL_CHARS:,})")
    return IntakeEmail(source_file=path.name, text=text)
