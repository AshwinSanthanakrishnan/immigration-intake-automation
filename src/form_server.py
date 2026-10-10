"""A stand-in for the firm's intake system.

Serves intake_form/ and accepts submissions at POST /api/intake, issuing a case ID for each.
It binds to 127.0.0.1 on an OS-assigned port, so it is unreachable from other machines, and
keeps submissions in memory only. In production the form filler would point at the firm's real
intake system instead.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CASE_TYPES = {"H-1B", "Family Green Card", "Naturalization", "Other"}
URGENCY_LEVELS = {"low", "medium", "high"}
REQUIRED = ("full_name", "email", "phone", "country_of_citizenship", "date_of_birth", "case_type", "urgency")
CHOICES = {
    "marital_status": {"single", "married", "divorced", "widowed", "separated"},
    "preferred_contact_method": {"email", "phone"},
    "consultation_requested": {"yes", "no"},
}
MAX_BODY_BYTES = 64 * 1024
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_submission(data: dict[str, Any]) -> list[str]:
    """Server-side checks, independent of the browser's own form validation."""
    problems = [f"{name} is required" for name in REQUIRED if not str(data.get(name) or "").strip()]
    if data.get("case_type") and data["case_type"] not in CASE_TYPES:
        problems.append("case_type is not a recognised case type")
    if data.get("urgency") and data["urgency"] not in URGENCY_LEVELS:
        problems.append("urgency must be low, medium, or high")
    for name in ("date_of_birth", "status_expires_on"):
        if data.get(name) and not _ISO_DATE_RE.match(str(data[name])):
            problems.append(f"{name} must be YYYY-MM-DD")
    for name, allowed in CHOICES.items():
        if data.get(name) and data[name] not in allowed:
            problems.append(f"{name} must be one of {', '.join(sorted(allowed))}")
    return problems


def new_case_id(now: datetime | None = None) -> str:
    return f"IMM-{(now or datetime.now()):%Y%m%d}-{secrets.token_hex(2).upper()}"


class _IntakeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: Any) -> None:
        super().__init__(address, handler)
        self.submissions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()


class _IntakeHandler(SimpleHTTPRequestHandler):
    server: _IntakeHTTPServer

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path != "/api/intake":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request size"})
            return
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be a JSON object"})
            return

        if problems := validate_submission(data):
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "Submission rejected: " + "; ".join(problems), "problems": problems},
            )
            return

        with self.server.lock:
            case_id = new_case_id()
            while case_id in self.server.submissions:
                case_id = new_case_id()
            self.server.submissions[case_id] = {
                **data,
                "case_id": case_id,
                "received_at": datetime.now().isoformat(timespec="seconds"),
            }
        log.info("Intake system recorded case %s", case_id)
        self._send_json(HTTPStatus.CREATED, {"case_id": case_id})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        log.debug("intake-server: " + format, *args)


class IntakeServer:
    def __init__(self, form_dir: Path) -> None:
        if not (form_dir / "index.html").is_file():
            raise FileNotFoundError(f"Intake form not found: {form_dir / 'index.html'}")
        self._form_dir = form_dir
        self._httpd: _IntakeHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("Intake server is not running")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> IntakeServer:
        handler = partial(_IntakeHandler, directory=str(self._form_dir))
        self._httpd = _IntakeHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="intake-server", daemon=True)
        self._thread.start()
        log.info("Local intake system running at %s", self.url)
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def get_submission(self, case_id: str) -> dict[str, Any] | None:
        if self._httpd is None:
            return None
        with self._httpd.lock:
            return self._httpd.submissions.get(case_id)

    def __enter__(self) -> IntakeServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
