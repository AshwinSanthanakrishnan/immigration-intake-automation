#!/bin/bash
# Run the Immigration Intake Automation pipeline
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -f ".venv/bin/python" ]; then
    echo "Virtual environment not found. Creating .venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/playwright install chromium
fi

echo "Running Immigration Intake Automation..."
exec .venv/bin/python main.py "$@"
