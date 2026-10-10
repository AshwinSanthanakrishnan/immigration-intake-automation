"""Rule-based stand-in for Claude, used with --offline.

It lets the browser, verification, and reporting steps be demoed and smoke-tested without an
API key. It is deliberately simple (keywords and regexes, no AI), and its output goes through
exactly the same Pydantic validation as Claude's. Nothing here is meant for production use.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from email.utils import parseaddr

from .models import REQUIRED_FIELDS, CaseType, IntakeExtraction

_COUNTRIES: dict[str, tuple[str, ...]] = {
    "Brazil": ("brazilian",),
    "Canada": ("canadian",),
    "China": ("chinese",),
    "Colombia": ("colombian",),
    "France": ("french",),
    "Ghana": ("ghanaian",),
    "India": ("indian",),
    "Japan": ("japanese",),
    "Kenya": ("kenyan",),
    "Mexico": ("mexican",),
    "Nigeria": ("nigerian",),
    "Pakistan": ("pakistani",),
    "Philippines": ("filipino", "filipina"),
    "South Korea": ("south korean", "korean"),
    "Taiwan": ("taiwanese",),
    "Ukraine": ("ukrainian",),
    "United Arab Emirates": ("emirati", "uae"),
    "United Kingdom": ("british",),
    "Vietnam": ("vietnamese",),
}
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NAME_RE = re.compile(
    r"\b(?:my name is|this is|candidate|client(?:\s+full)?\s+name|applicant(?:\s+legal)?\s+name|beneficiary\s+name|full\s+(?:legal\s+)?name)\s*[:\-]?\s*([a-z][a-z'-]+(?:[ \t]+[a-z][a-z'-]+){1,2})",
    re.I,
)
_DOB_RE = re.compile(r"\b(?:date of birth|dob|born(?: on)?)\s*[:\-]?\s*([A-Za-z0-9 ,/.-]{6,30})", re.I)
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d")
_HIGH_RE = re.compile(
    r"\b(urgent|asap|as soon as possible|deadline|expires?|expiring|expired|hearing|"
    r"court date|detained|detention|emergency)\b"
)
_LOW_RE = re.compile(r"\b(no (?:big )?rush|no hurry|not urgent|just curious|general question)\b")
_FAMILY_RE = re.compile(r"\b(spouse|husband|wife|married|marriage|parent|mother|father|son|daughter|sibling|brother|sister)\b")
_MONTH_DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
_EXPIRY_RE = re.compile(rf"\b(?:expires?|expiring|ends?|valid until)(?: on)?\s+({_MONTH_DATE})")
_EMPLOYER_RE = re.compile(r"\bemployer, ([A-Z][\w&.-]*(?: [A-Z][\w&.-]*)*)")
_LOCATION_RE = re.compile(
    r"\b(?:living|working|based|live|reside|residing) in ([A-Z][a-z]+(?: [A-Z][a-z]+)?, [A-Z][a-z]+(?: [A-Z][a-z]+)?)"
    r"|\bhere in ([A-Z][a-z]+(?: [A-Z][a-z]+)?)"
)
_OCCUPATION_RE = re.compile(
    r"\b((?:[A-Za-z]+ ){0,2}(?:engineer|architect|developer|researcher|scientist|analyst|nurse|physician|"
    r"teacher|professor|accountant|designer|manager))\b",
    re.I,
)
_EDUCATION_RE = re.compile(
    r"\b((?:M\.S\.|B\.S\.|M\.A\.|B\.A\.|MBA|Ph\.D\.|Master of [A-Z][A-Za-z]+|Bachelor of [A-Z][A-Za-z]+)"
    r"(?: in [A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)*)?)"
)
# Checked in order; the first match wins.
_STATUS_RULES: tuple[tuple[str, str], ...] = (
    (r"\bstem opt\b", "F-1 (STEM OPT)"),
    (r"\bf-1\b", "F-1"),
    (r"\bj-1\b", "J-1"),
    (r"\b(?:lawful )?permanent resident\b|\bgreen card since\b", "Permanent resident"),
    (r"\bh-1b (?:extension|status)\b|\bcurrent h-1b\b", "H-1B"),
)
_SUBTYPE_RULES: tuple[tuple[str, str], ...] = (
    (r"\bh-?1b transfer\b", "H-1B transfer"),
    (r"\bh-?1b (?:3-year )?extension\b", "H-1B extension"),
    (r"\bcap petition\b|\blottery\b", "H-1B cap petition"),
    (r"\badjustment of status\b", "Marriage-based adjustment of status"),
    (r"\b3-year marital rule\b|\bmarried to a u\.s\. citizen for over 3 years\b", "N-400 (3-year marriage rule)"),
    (r"\basylum\b|\bnotice to appear\b", "Asylum / removal defense"),
)
_CASE_LABELS = ("case type", "case category", "case classification", "matter")
_MARITAL = {"single", "married", "divorced", "widowed", "separated"}

_MATTER_WORDING = {
    CaseType.H1B: "H-1B",
    CaseType.FAMILY_GREEN_CARD: "family green card",
    CaseType.NATURALIZATION: "naturalization",
    CaseType.OTHER: "immigration",
}

_CHECKLISTS: dict[CaseType, str] = {
    CaseType.H1B: """\
## Identity and immigration history
- [ ] Passport (biographic page, valid at least six months)
- [ ] Current and prior U.S. visas and the most recent I-94 record
- [ ] Prior approval notices (I-797), I-20s, and EAD cards, if any

## Education and experience
- [ ] Degree certificates and transcripts
- [ ] Credential evaluation for any foreign degree
- [ ] Resume and experience letters from prior employers
- [ ] Professional licenses, if the occupation requires one

## Employment (largely provided by the employer)
- [ ] Signed offer letter and detailed job description
- [ ] Recent pay stubs, if currently employed in the U.S.

## Notes for the reviewing attorney
- Confirm current status and expiration dates to plan around any gap in work authorization.
- Dependents (H-4) need their own identity and relationship documents.""",
    CaseType.FAMILY_GREEN_CARD: """\
## Identity and civil documents
- [ ] Passport and birth certificate, with certified English translations
- [ ] Passport-style photos

## Relationship evidence
- [ ] Marriage or birth certificate establishing the qualifying relationship
- [ ] Proof that any prior marriages ended (divorce decrees, death certificates)
- [ ] Evidence of a genuine marriage, if spouse-based (joint lease, accounts, photos)

## Petitioner and sponsor
- [ ] Petitioner's proof of U.S. citizenship or permanent residence
- [ ] Sponsor's recent tax returns or transcripts, W-2s, and employment letter

## Immigration history
- [ ] I-94 record and evidence of lawful entry
- [ ] Prior visas, approval notices, and any immigration court documents

## Notes for the reviewing attorney
- Whether the client adjusts status in the U.S. or processes abroad changes the list.
- A medical exam by a designated civil surgeon is usually needed; timing varies.""",
    CaseType.NATURALIZATION: """\
## Identity and status
- [ ] Permanent resident card (front and back)
- [ ] Passport(s) covering the statutory period

## Residence and travel history
- [ ] Dates of all trips outside the U.S. during the statutory period
- [ ] Home addresses and employers for the statutory period
- [ ] Federal tax transcripts for the statutory period

## Other records, if applicable
- [ ] Marriage, divorce, or death certificates for current and prior marriages
- [ ] Court dispositions for any arrest, citation, or charge
- [ ] Selective Service registration record
- [ ] Proof of child support payments

## Notes for the reviewing attorney
- Trips abroad of six months or more can raise continuous-residence questions.
- The statutory period is shorter for some applicants married to U.S. citizens.""",
    CaseType.OTHER: """\
## Identity and status
- [ ] Passport and any other government-issued ID
- [ ] Current visa, I-94 record, and any EAD or green card

## Immigration history
- [ ] All notices received from USCIS, the State Department, or immigration court
- [ ] Copies of any previously filed applications or petitions

## Notes for the reviewing attorney
- Case type was not one of the standard categories; refine this list after consultation.""",
}


class OfflineAI:
    name = "Offline rule-based stand-in (no AI calls)"

    def extract(self, email_text: str, previous_error: str | None = None) -> str:
        headers, body = _split_email(email_text)
        from_name, from_address = parseaddr(headers.get("from", ""))
        data = {
            "full_name": _full_name(from_name, body),
            "email": from_address if "@" in from_address else _first_match(_EMAIL_RE, body),
            "phone": _first_match(_PHONE_RE, body),
            "country_of_citizenship": _country(body),
            "date_of_birth": _date_of_birth(body),
            "case_type": (_labeled_case_type(body) or _case_type(body)).value,
            "urgency": _labeled_urgency(body) or _urgency(body),
        }
        data["missing_fields"] = [name for name in REQUIRED_FIELDS if data[name] is None]
        data.update(_optional_details(headers, body, data["case_type"]))
        return json.dumps(data)

    def draft_followup(self, email_text: str, record: IntakeExtraction) -> str:
        first_name = record.full_name.split()[0] if record.full_name else "there"
        matter = _MATTER_WORDING[record.case_type]
        items = "\n".join(f"- {label}" for label in record.missing_field_labels)
        return (
            "Subject: Your inquiry - a few more details needed\n\n"
            f"Hi {first_name},\n\n"
            f"Thank you for contacting our office about your {matter} matter. Before we can open "
            "an intake record for you, could you please send us the following:\n\n"
            f"{items}\n\n"
            "You can simply reply to this email with the details.\n\n"
            "Best regards,\nClient Intake Team"
        )

    def draft_checklist(self, case_type: CaseType, country_of_citizenship: str | None) -> str:
        return _CHECKLISTS[case_type]

    def usage_summary(self) -> str | None:
        return None


def _split_email(text: str) -> tuple[dict[str, str], str]:
    """Split the top header block from the body. If no email headers exist, entire text is body."""
    head, sep, rest = text.partition("\n\n")
    headers = {}
    if "from:" in head.lower() or "to:" in head.lower():
        for line in head.splitlines():
            key, s, value = line.partition(":")
            if s:
                headers[key.strip().lower()] = value.strip()
        body = rest
    else:
        body = text
    return headers, body


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def _full_name(from_name: str, body: str) -> str | None:
    if len(from_name.split()) >= 2:
        return from_name.strip()
    match = _NAME_RE.search(body)
    if not match:
        return None
    name = match.group(1)
    return name.title() if name.islower() else name


def _country(body: str) -> str | None:
    text = body.lower()
    for country, demonyms in _COUNTRIES.items():
        c_lower = country.lower()
        if re.search(rf"\b(?:country of citizenship|citizenship|citizen|national)(?:\s+of)?\s*[:\-]?\s*(?:the )?{c_lower}\b", text):
            return country
        for demonym in demonyms:
            if re.search(rf"\b(?:i'?m|i am)\s+(?:an?\s+)?{demonym}\b", text) or re.search(
                rf"\b{demonym} (?:citizen|national|passport)\b", text
            ):
                return country
    return None


def _date_of_birth(body: str) -> str | None:
    for match in _DOB_RE.finditer(body):
        candidate = match.group(1).strip(" ,.-")
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def _case_type(body: str) -> CaseType:
    text = body.lower()
    if re.search(r"\bh-?1b\b", text):
        return CaseType.H1B
    if re.search(r"marriage|spouse|husband|wife|adjustment of status", text) and "green card" in text:
        return CaseType.FAMILY_GREEN_CARD
    if re.search(r"naturali[sz]|citizenship|n-400|become a (?:u\.?s\.? )?citizen", text):
        return CaseType.NATURALIZATION
    if "green card" in text and _FAMILY_RE.search(text):
        return CaseType.FAMILY_GREEN_CARD
    return CaseType.OTHER


def _urgency(body: str) -> dict[str, str]:
    text = body.lower()
    if match := _HIGH_RE.search(text):
        return {"level": "high", "reason": f"Email mentions '{match.group(0)}' (keyword rule)."}
    if match := _LOW_RE.search(text):
        return {"level": "low", "reason": f"Email says '{match.group(0)}' (keyword rule)."}
    return {"level": "medium", "reason": "No deadline or time-pressure keywords found (keyword rule)."}


def _labeled(body: str, *labels: str) -> str | None:
    """The value after "Label:" at the start of a line (optionally a "- " bullet)."""
    names = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"^[ \t]*(?:-[ \t]*)?(?:{names})[ \t]*:[ \t]*(.+)$", body, re.I | re.M)
    return match.group(1).strip() if match else None


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _first_rule(rules: tuple[tuple[str, str], ...], text: str) -> str | None:
    return next((value for pattern, value in rules if re.search(pattern, text)), None)


def _optional_details(headers: dict[str, str], body: str, case_type: str) -> dict[str, object]:
    text = body.lower()

    location = _labeled(body, "current location", "location", "current address", "city")
    if location is None and (match := _LOCATION_RE.search(body)):
        location = match.group(1) or match.group(2)

    expiry = None
    if match := _EXPIRY_RE.search(body):
        try:
            expiry = datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass

    occupation = _labeled(body, "occupation", "job title", "position")
    if occupation is None and (match := _OCCUPATION_RE.search(body)):
        occupation = re.sub(r"^(?:an?|the|as|work|i)\s+", "", match.group(1), flags=re.I)
        occupation = occupation[0].upper() + occupation[1:]

    education = _labeled(body, "education", "highest education", "degree")
    if education is None and (match := _EDUCATION_RE.search(body)):
        education = match.group(1)

    subtype = None
    labeled_case = _labeled(body, *_CASE_LABELS)
    if labeled_case and labeled_case.lower() != case_type.lower():
        subtype = labeled_case
    subtype = subtype or _first_rule(_SUBTYPE_RULES, text)

    marital = (_labeled(body, "marital status") or "").split()[0:1]
    marital = marital[0].lower() if marital and marital[0].lower() in _MARITAL else None
    if marital is None and re.search(r"\b(?:my (?:husband|wife|spouse)|i married|i am married|we got married|married to)\b", text):
        marital = "married"

    contact = (_labeled(body, "preferred contact", "preferred contact method") or "").lower() or None
    if contact not in (None, "email", "phone"):
        contact = None
    if contact is None and re.search(r"\bemail is the best way\b|\bprefer(?:red)? (?:to be contacted by )?email\b", text):
        contact = "email"
    elif contact is None and re.search(
        r"\b(?:phone|call) is the best way\b|\bprefer(?:red)? (?:to be contacted by |a )?(?:call|phone)\b", text
    ):
        contact = "phone"

    summary = _labeled(body, "notes") or headers.get("subject")
    return {
        "country_of_birth": _labeled(body, "country of birth", "place of birth"),
        "current_location": location,
        "marital_status": marital,
        "preferred_contact_method": contact,
        "current_immigration_status": _labeled(body, "current status", "status", "current visa")
        or _first_rule(_STATUS_RULES, text),
        "status_expires_on": expiry,
        "occupation": occupation,
        "employer": _labeled(body, "employer", "company") or _first_group(_EMPLOYER_RE, body),
        "highest_education": education,
        "case_subtype": subtype,
        "consultation_requested": True if re.search(r"\bconsultation\b", text) else None,
        "matter_summary": summary,
    }


def _labeled_case_type(body: str) -> CaseType | None:
    """A form-style "Case Type: ..." line beats keyword guessing over the whole text."""
    value = (_labeled(body, *_CASE_LABELS) or "").lower()
    if not value:
        return None
    if value.startswith("other"):
        return CaseType.OTHER
    if re.search(r"\bh-?1b\b", value):
        return CaseType.H1B
    if "naturali" in value or "n-400" in value:
        return CaseType.NATURALIZATION
    if "green card" in value or "adjustment of status" in value:
        return CaseType.FAMILY_GREEN_CARD
    return None


def _labeled_urgency(body: str) -> dict[str, str] | None:
    value = _labeled(body, "urgency")
    match = re.match(r"(low|medium|high)\b[\s.:-]*(.*)", value or "", re.I)
    if not match:
        return None
    reason = match.group(2).strip() or f"Client marked urgency as {match.group(1).lower()}."
    return {"level": match.group(1).lower(), "reason": f"{reason[0].upper()}{reason[1:]} (stated by client)."}
