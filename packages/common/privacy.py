"""Personal-data check for anything that leaves the platform as research data (ADR-014).

Accounts and the database may hold people's names and contact details; a dataset
export may not. `find_personal_data` walks a JSON-like value and reports:

- **fields** whose name says they hold personal data (`name`, `email`, `dob`, …),
  wherever they are nested;
- **values** that contain a known personal string — the participants' names,
  nicknames and emails, passed in by the caller — in any string, key or file name.

The export runs it over every file it writes and refuses to finish if anything is
found, so a new field that carries a name can't slip into a dataset unnoticed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Field names that hold personal data, compared after lower-casing and dropping
# "_", "-" and spaces (so "Full Name", "full_name" and "fullName" all match).
PERSONAL_FIELDS = frozenset(
    {
        "name",
        "fullname",
        "firstname",
        "lastname",
        "surname",
        "nickname",
        "fightername",
        "coachname",
        "ratername",
        "username",
        "fighter",
        "coach",
        "email",
        "emailaddress",
        "phone",
        "phonenumber",
        "mobile",
        "dob",
        "dateofbirth",
        "birthdate",
        "birthday",
        "address",
        "streetaddress",
        "postcode",
        "zipcode",
        "nationalid",
        "idnumber",
        "passport",
        "passportnumber",
        "ssn",
        "licensenumber",
        "boxrecid",
        "usaboxingid",
        "emergencycontact",
        "insurance",
        "insurancepolicy",
    }
)

MIN_KNOWN_LENGTH = 3  # shorter strings ("Al", "Jo") would match everywhere


class PersonalDataError(ValueError):
    """Raised when research data would carry personal data."""

    def __init__(self, findings: list[str]) -> None:
        self.findings = findings
        super().__init__("personal data found: " + "; ".join(findings[:5]))


def _norm_field(key: str) -> str:
    return re.sub(r"[\s_\-]", "", key).lower()


def is_personal_field(key: str) -> bool:
    return _norm_field(key) in PERSONAL_FIELDS


def _patterns(values: Iterable[str | None]) -> list[re.Pattern[str]]:
    """Each known string as a whole-word, case-insensitive pattern: "Tom" matches
    "tom_jab" or "Tom's phone" but not "bottom" (letters and digits either side
    make it part of another word; "_", "-", "." and spaces don't)."""
    words = {v.strip() for v in values if v and len(v.strip()) >= MIN_KNOWN_LENGTH}
    return [
        re.compile(rf"(?<![^\W_]){re.escape(w)}(?![^\W_])", re.IGNORECASE)
        for w in sorted(words, key=len, reverse=True)
    ]


def find_personal_data(
    value: Any, known: Iterable[str | None] = (), *, where: str = "$"
) -> list[str]:
    """Where `value` carries personal data — `[]` when it's clean. `known` are the
    names/emails to look for; `where` labels the findings (a file name, say)."""
    needles = _patterns(known)
    found: list[str] = []

    def text(s: str, at: str) -> None:
        if any(n.search(s) for n in needles):
            found.append(f"{at}: contains a participant's name or email")

    def walk(v: Any, at: str) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                key = str(k)
                if is_personal_field(key):
                    found.append(f"{at}.{key}: personal-data field")
                text(key, f"{at}.{key}")
                walk(child, f"{at}.{key}")
        elif isinstance(v, list | tuple):
            for i, child in enumerate(v):
                walk(child, f"{at}[{i}]")
        elif isinstance(v, str):
            text(v, at)

    walk(value, where)
    return found


def check_no_personal_data(
    value: Any, known: Iterable[str | None] = (), *, where: str = "$"
) -> None:
    """`find_personal_data`, raising `PersonalDataError` if anything is found."""
    found = find_personal_data(value, known, where=where)
    if found:
        raise PersonalDataError(found)
