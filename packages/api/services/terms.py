"""The platform's terms and conditions (ADR-016).

Everyone accepts the current version: at sign-up, and — for accounts made before
it, made by an admin, or after the terms change — at their next sign-in. Until
they do, the API refuses their requests (`auth.require_current_user`).

The text is `terms.md` next to this file. To change the terms, replace the text
and raise `TERMS_VERSION`: everyone is asked again.
"""

from __future__ import annotations

from pathlib import Path

# Raise whenever terms.md changes; anything not "draft-…" is a real version.
TERMS_VERSION = "draft-1"
TERMS_FILE = Path(__file__).with_name("terms.md")


def text() -> str:
    return TERMS_FILE.read_text()


def is_placeholder() -> bool:
    return TERMS_VERSION.startswith("draft-")
