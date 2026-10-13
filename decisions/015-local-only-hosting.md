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

### Nothing else calls out either

- **MediaPipe is served by the dashboard.** Camera pages used to fetch the pose
  engine from jsDelivr (unversioned — whatever was latest, not the 0.10.x the
  app is built against) and the pose model from Google storage on every open.
  `scripts/mediapipe-assets.mjs` now copies the engine from the installed
  `@mediapipe/tasks-vision` and the models from `models/mediapipe/` into
  `public/mediapipe/` before every dev or build (`predev*`, `prebuild`);
  `lib/mediapipe.ts` points the pages there. Recording works without internet.
- **Next.js sends no usage data**: every `next` script runs with
  `NEXT_TELEMETRY_DISABLED=1`.
- **YOLO sends no usage statistics**: `pose_yolo` turns Ultralytics' `sync`
  setting off before a model loads, whatever the machine's setting.
- `tests/unit/test_local_only.py` keeps it that way: no outside host in the
  dashboard source, usage data off in every Next script, MediaPipe copied before
  dev and build, YOLO's statistics off.

Still fetched once at setup, never per use: the pose models
(`ensure_pose_model`, from Google storage, only if `models/mediapipe/` lacks
them) and YOLO weights (from GitHub, only if the `.pt` file is missing).

## Alternatives considered

- **Load MediaPipe from the CDN, pinned to a version.** Fixes the version drift
  but still contacts jsDelivr and Google from every camera, and needs internet.
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
