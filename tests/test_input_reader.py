"""Tests for input_reader reading txt and pdf files."""

from __future__ import annotations

from pathlib import Path
import pytest
from src.input_reader import list_intake_files, read_intake_email, InputError


def test_list_and_read_sample_inputs():
    folder = Path("sample_inputs")
    files = list_intake_files(folder)
    assert len(files) >= 10

    # Ensure both txt and pdf are discovered
    suffixes = {f.suffix.lower() for f in files}
    assert ".txt" in suffixes
    assert ".pdf" in suffixes

    # Read each file without error
    for f in files:
        doc = read_intake_email(f)
        assert doc.source_file == f.name
        assert len(doc.text) > 0


def test_empty_file_raises_input_error(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    with pytest.raises(InputError, match="empty"):
        read_intake_email(empty)
