# 014. De-identified datasets — participant codes, a checked export, withdrawal by code

- **Status**: Accepted (step 2, face-blurred video in the export, added 2026-10-13)
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

### Step 2 — face-blurred video in the export

Video goes into the export only as a face-blurred copy (`capture.cv.face_blur`,
`api.services.video_blur`). A face is wherever either of two sources puts one,
and both are always pixelated:

- **pose** — the clip's own MediaPipe landmarks: face points for the centre,
  shoulders for the size and the chin (so the box holds when the head turns),
  joined over ±150 ms of pose frames (so a fast move doesn't outrun it). Finds
  the fighter even behind the gloves.
- **YuNet** (OpenCV model zoo, `models/face/`, fetched once and checked against
  its sha256) — side-on faces, jaw and beard, and anyone else in view.

**Fail closed**: a frame where neither finds a face is pixelated whole; a clip
that can't be blurred (unreadable, encoding fails, the output check fails) is
left out of the export and named in its warnings — never exported as recorded.
Each copy is checked after encoding: one video stream, every frame, no metadata
tags. It comes with `{camera}.frames.csv` (each frame's t_ms on the take
timeline; the WebM's variable frame rate can't survive a re-encode) and a strip
of blurred close-ups for the researcher to check.

Copies live in `{dataset}/blurred/{take}/`, made once and redone only when the
recording or `BLUR_VERSION` changes. The export starts blurring what's pending
in the background (about half the video's length); the dataset page shows
progress and exports again when it's done. The Train button's export doesn't
start it — training needs no video. Deleting a take or withdrawing a participant
removes their blurred copies too.

A trial on 5 clips from all three cameras: pose found a face in 99–100% of
frames, YuNet in 99.9–100%; YuNet's box reached past pose's in almost every
frame (pose alone left jaw or beard showing side-on); no frame needed the
whole-frame fallback; the only false alarm was a raised fist (33 of ~4,200
frames). OpenCV's built-in Haar detector was rejected: it flagged a lamp
reflection and a curtain in most frames and missed side-on faces.

Known limit: a reflection or a person whose face isn't visible isn't found — and
needs no blurring; one whose face is visible is found by YuNet. Keep mirrors and
glass doors out of shot when recording.

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
- Follow-ups: terms acceptance at sign-up; the label provenance in `protocol.json` names its detector under a
  `name` field, so it isn't exported (labels carry a content-hash `version`).
