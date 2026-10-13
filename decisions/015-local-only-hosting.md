# 015. Local-only hosting — no automatic cloud deploys

- **Status**: Accepted
- **Date**: 2026-10-13
- **Phase**: 6

## Context

The researcher's rule: account details and recordings are never stored on a
public cloud or anywhere against regulation. The repository did the opposite by
default:

- every push to `main` deployed the API to Railway and the dashboard to Vercel
  (`.github/workflows/deploy.yml`, `railway.toml`) — an API on Railway stores its
  database and uploads there;
- `scripts/migrate_sqlite_to_pg.py` copied every table of the local database to a
  Railway Postgres, with that database's connection string, password included,
  committed in a public repository.

## Decision

The platform runs on the researcher's own machines only. The deploy workflow, the
Railway config and the Railway migration script are removed. The Docker image
stays for running the API locally (its data directory should be on an encrypted
volume). CI (`ci.yml`) still runs the tests on GitHub — it sees code, never data.

## Alternatives considered

- **Keep the deploys for a future production.** A deploy on every push is how
  data ends up in the cloud unnoticed; a future hosted setup needs its own
  decision (region, encryption, agreements) and can add a deploy then.

## Consequences

- Pushing to `main` no longer publishes anything.
- Removing the files doesn't undo what already exists: the Railway database's
  password is still in the repository's history and must be changed (or the
  database deleted) in Railway; the existing Railway and Vercel deployments must
  be shut down in their dashboards.
- The Capacitor app's `server.url` still points at the old Vercel address; phones
  use the dashboard over the LAN (`npm run dev:https`) instead.
