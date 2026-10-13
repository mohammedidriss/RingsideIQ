# 016. Terms and conditions — accepted before anything is used

- **Status**: Accepted (with placeholder text, to be replaced by the approved terms)
- **Date**: 2026-10-13
- **Phase**: 6

## Context

Accounts hold people's real names and emails (ADR-014), on the researcher's own
machines (ADR-015). The researcher's rule: anyone who signs up agrees to the
platform's terms and conditions first. Before this decision, sign-up asked for
nothing, and accounts made by an admin or by a gym never saw any terms.

## Decision

- **Every account records the terms it accepted**: `user.terms_version` and
  `user.terms_accepted_at` (migration `b4d8e2f6a1c3`; existing accounts start empty).
- **Sign-up requires accepting the current version**: the form shows a checkbox
  linking to `/terms`, and `POST /auth/register` refuses (422) without
  `accept_terms: true` and the current `terms_version`.
- **The API refuses everything else until the current terms are accepted**:
  `require_current_user` — which guards every data route — returns 403
  `terms_not_accepted` for anyone whose `terms_version` isn't current. Only
  `GET /auth/me`, `GET /auth/terms` (public) and `POST /auth/terms/accept` work
  before that (`require_signed_in`). The dashboard shows a blocking "Terms and
  conditions" screen in place of the app; the 403 makes it impossible to skip.
- **Changing the terms asks everyone again**: the text is
  `packages/api/services/terms.md`; raising `TERMS_VERSION` in `terms.py` makes
  every account accept the new version at its next request. Accepting a version
  other than the current one is refused (409).
- **The text is a placeholder** (`draft-1`, flagged as such by `GET /auth/terms`),
  marked in the text itself, to be replaced with the wording approved for the
  study before real participants sign up.

Phones joining a capture with a join token never sign in, so the gate doesn't
touch them.

## Alternatives considered

- **Check only in the dashboard.** A request straight to the API would skip it.
- **Ask only at sign-up.** Admin- and gym-made accounts, existing accounts and
  changed terms would never be asked.

## Consequences

- Every existing account — the researcher's included — sees the terms screen at
  its next request after this is applied, and can't use the platform until it
  accepts.
- `tests/integration/test_terms.py` covers sign-up, the first sign-in of an
  admin-made account, a terms change, and the public text.
- Follow-up: replace `terms.md` with the approved terms and set `TERMS_VERSION`
  to a real version (anything not starting with `draft-`).
