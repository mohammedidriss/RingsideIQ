# 014. De-identified datasets — participant codes, a checked export, withdrawal by code

- **Status**: Accepted (step 1 of 2 — face-blurred video in the export is step 2)
- **Date**: 2026-10-13
- **Phase**: 6 (datasets / RQ2)

## Context

The study's requirements (RingsideIQ Platform Requirements, R1–R4) were written as
"codes only, everywhere". The researcher narrowed them: **accounts may hold real
names and emails** — people sign up, accept the terms and log in with their
official details, and that stays in the local database — but **datasets must
carry nothing that identifies anyone**, and are never shared.

Before this decision a dataset did identify people:

- every take's `take.json` stored the fighter's name (and the dataset's name);
- the export manifest named fighters by database id, which links straight back
  to the account, and pointed at the raw take folders rather than a copy;
- clips were recorded with sound (`audio: true` in CameraNode; the iOS and
  Android apps declared the microphone) and show faces;
- nothing checked an export for personal data, and a participant could not be
  removed from a dataset as a unit.

## Decision

1. **Participant codes.** Each dataset participant gets a code — `F-0001`,
   `F-0002`, … in the order they join — stored on `dataset_participant.code`
   (unique per dataset, migration `9c1e5a7d3f42` numbers existing ones). The
   dataset's files name people by code only: `take.json` carries `participant`
   and no name. The dataset page still shows names next to codes, read from the
   database at display time.
2. **The export is a de-identified copy.** `POST /v2/datasets/{id}/export` writes
   `export/manifest.json` plus `export/takes/{take}/` with a rebuilt `take.json`
   (code, stance, block, timing — an allow-list), `labels.json`, `imu.csv`,
   `hr.csv` and pose. Sensor and pose files are hard-linked (no extra disk).
   `protocol.json`, device labels (a phone may carry its owner's name) and the
   dataset name stay behind. Training reads only this copy.
3. **A personal-data check guards every export** (`common.privacy`). Each staged
   file is scanned for personal-data field names (`name`, `email`, `dob`, …,
   however spelled or nested) and for the participants' own full names,
   nicknames, birth dates, IDs and account emails anywhere in JSON, CSV headers
   and file names (whole-word matches). Anything found refuses the export, and the
   previous export stays as it was.
4. **Video stays out of the export** until it's face-blurred (step 2: faces are
   blurred in the exported copy; the recordings on this machine stay as they are,
   since pose recovery and label review need them). Clips are recorded **video
   only** from now on: the microphone is never requested, the recorder takes the
   video track alone, and the apps no longer declare the microphone.
5. **Withdrawal by code.** `POST /v2/datasets/{id}/participants/{code}/withdraw`
   deletes all of the participant's takes (video, pose, wrist and heart data,
   labels) and their copies in the export (manifest and splits drop them), marks
   them withdrawn so they can't be recorded again (the code is never reused), and
   appends a line to `data/datasets/{id}/deletions.jsonl` — code, time, counts, no
   data. Models already trained are not changed.

## Alternatives considered

- **Codes only, everywhere (the original R1.1/R1.3).** Rejected by the researcher:
  accounts need real names to log in, and they stay local.
- **Code on the fighter instead of per dataset.** A per-dataset code keeps two
  datasets unlinkable by code, and consent is already per dataset.
- **Export as a manifest pointing at the raw folders (as before).** The raw
  folders hold video with faces and, until now, names; training could reach them.
- **Blur faces at recording.** Strongest, but a large browser change and risky on
  phones, and pose recovery would see blurred faces. Blurring the export copy
  meets "the dataset carries no identity" with the raw recordings kept local.

## Consequences

- An export can't carry a name or contact field without being refused; the check
  is tested (`tests/unit/test_privacy.py`, `tests/integration/test_dataset_privacy.py`).
- Recordings made before this change still have sound and faces on this machine;
  they never reach the export (no video is exported until step 2 blurs it).
- `dataset_participant.code` needs `alembic upgrade head` on existing databases.
- Follow-ups: step 2 (face-blurred video in the export); terms acceptance at
  sign-up; the label provenance in `protocol.json` names its detector under a
  `name` field, so it isn't exported (labels carry a content-hash `version`).
