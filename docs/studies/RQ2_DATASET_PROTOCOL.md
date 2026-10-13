# RQ2 dataset protocol — recording and labeling the punch dataset

How Alion's in-domain punch dataset is recorded, labeled, checked and split.
Recording happens in Alion's **Datasets** area, separate from training sessions
(ADR-013): a dataset holds participants and their consent, and each recording
is a **take** with its own folder.
It supplies the ground truth for RQ2 (detection F1 ≥ 0.80 — H2a; 6-class
type macro-F1 ≥ 0.70 — H2b; timing — H2c) and the training data for the
in-domain punch detector and type classifier.

## 1. Why this protocol

The current LSTM was trained on public Olympic footage and misfires on our
setup (domain shift), and the stored punch events are the heuristic's own
predictions, not ground truth. The plan in `DISSERTATION.md` cut clips with
`scripts/ml/split_punch_video.py`, which finds punches from **CV wrist
velocity** — so the labels would come from the signal under evaluation. That is
circular, and an examiner will say so.

This protocol replaces it. Two independent sources make every label:

- **The block** gives the punch type and hand (the fighter throws 30 jabs, then
  30 crosses, …).
- **The wrist IMUs** — a separate sensor, not the camera — give each punch's time.

The camera pipeline never contributes to its own ground truth.

## 2. Ethics and data handling

- **Self-recording first.** ADR-002 allows no real-athlete data until the
  Phase 8 encryption work. The researcher's own takes are self-test data and
  can be recorded now.
- **Other fighters** only after IRB approval, signed consent, and the
  encryption work.
- **Consent is enforced.** Each dataset lists its participants with a consent
  status (`self`, `irb_signed` or `pending`). Alion refuses to create a take
  for anyone who isn't `self` or `irb_signed`.
- **Storage.** Raw video goes on an **encrypted** external SSD (APFS
  Encrypted, 1–2 TB), not the laptop's internal disk (34 GB free).

## 3. Equipment and setup

| Item | Setting |
|---|---|
| Wrist IMUs | 2 × WitMotion WT901BLECL — 100 Hz, 42 Hz low-pass, ±16 g, calibrated (`scripts/imu_tool.py`) |
| Cameras | Laptop (front) + 1–3 phones joined by the take's QR; one phone side-on (~90°) |
| Heart rate | Polar H10, optional for this dataset |
| Fighter | Gloves on, one stance for the whole take (recorded from the fighter profile) |

- **Sensor placement.** Strap each unit on the back of the wrist, just above
  the glove cuff. The **left** unit goes on the left wrist and the **right** on
  the right (assigned in `data/imu/devices.json`). Keep the same orientation
  every take: label facing out, toward the back of the hand.
- **Framing.** The whole body is in view of every camera, head to feet, with
  even light and no one else in frame.
- **Before recording:** in the fighter's **IMU** tab, press **Check sensors**.
  Both wrists should read ~100 Hz, ~1.00 g at rest, and battery above 20%.

## 4. Take procedure (in Alion)

**Record by category (the default).** Every block is its own short take: on
the dataset page, each participant has a card per block (Jab, Cross, …, 1-2,
No punches, Free). **Record** on a card records just that block in automatic
mode, saves when the block ends, and shows the take's checks. Then you choose
**Record another**, **Redo** (deletes that take and records the block again),
or go back to the categories. Nothing advances on its own. A 50-second take
that goes wrong costs 50 seconds, not a 16-minute sitting. The card
shows how many takes each block has.

A full-protocol take (all blocks in one recording) works the same way, with
the steps below:

1. **Datasets** → open the dataset → make sure the fighter is a participant
   with consent → start a **new take**. Phones scan the take's QR.
2. Press **Start all cameras**. The wrist sensors start with them; the live
   reader shows both wrists moving.
3. In the **Dataset protocol** card, press **Start** on a block. The fighter
   does the block, then you press **End**. Rest 20–30 s between blocks while
   the cameras keep rolling — takes can't be paused, which keeps every stream
   on one unbroken timeline.
4. **Check the count** after each typed block: 30 thrown should read ~30
   (green within ±2, amber otherwise). If it's off, or the block went wrong,
   press **Redo** and record it again — the discarded attempt drops out of the
   labels. The check only works if the fighter throws **exactly one punch per
   beep and returns to guard**. On the first take the jab was doubled (two per
   beep) and crosses followed the fighter's own ~1.2 s rhythm, so those counts
   were right but didn't read 30.
5. After the last block, press **Stop & save**.

### Blocks

| # | Block | Target | Labeled as | Purpose |
|---|---|---|---|---|
| 1 | Jab | 30 reps | `jab`, lead hand | type class 1 |
| 2 | Cross | 30 reps | `cross`, rear hand | type class 2 |
| 3 | Lead hook | 30 reps | `hook`, lead hand | type class 3 |
| 4 | Rear hook | 30 reps | `hook`, rear hand | type class 4 |
| 5 | Lead uppercut | 30 reps | `uppercut`, lead hand | type class 5 |
| 6 | Rear uppercut | 30 reps | `uppercut`, rear hand | type class 6 |
| 7 | 1-2 | 15 combos, 3 s apart | jab (lead), cross (rear) | punches in sequence |
| 8 | 1-2-3 | 15 combos, 3 s apart | jab, cross, lead hook | punches in sequence |
| 9 | 1-1-2 | 15 combos, 3 s apart | jab, jab (lead), cross | same-hand double |
| 10 | 3-2 | 15 combos, 3 s apart | lead hook, cross | hook into a straight |
| 11 | No punches | 2 min | — (no labels) | negatives: guard, footwork, slips, rolls, feints |
| 12 | Free shadowboxing | 2 min | timed, type left blank | realistic test material |

The six classes are punch type × lead/rear. Lead is the left hand for orthodox,
the right for southpaw. Switch fighters record each stance as a separate
take. Throw each single punch from guard and return to guard, one per beep.
In the combo blocks, throw the whole combination on each beep at natural
speed, then return to guard before the next beep. One take yields ~180
single punches, ~150 punches in combos, and ~2 min of each of negatives and
free work.

## 5. How labels are made

Every time a block ends, Alion relabels the take into `{take}/labels.json`.
This is the format `studies.evaluation.load_labels` (and `scripts/evaluate.py`)
already score against:

```json
[{"t_ms": 1032.0, "hand": "left", "punch_type": "jab"}, …]
```

- **Timeline.** Blocks, IMU samples, pose frames and clips share one clock: ms
  since the cameras' synchronized start. Every camera starts recording at that
  instant and records how late its first frame actually was
  (`start_offset_ms`; 0.4–0.7 s on the first take, as the phone's encoder
  spins up), so video lines up with the sensors exactly once the offset is
  applied. IMU sample times are reconstructed from the sensor's 100 Hz
  grid (`capture.imu.clock.SampleClock`: mean error 2.6 ms, max 4.7 ms).
- **Detector** (`analyze.imu_punches`). It works on dynamic acceleration
  ||a| − 1 g| (gravity removed whatever the wrist's tilt), smoothed over 50 ms.
  By default a burst at or above 1.5 g is a punch, and the tallest burst within
  350 ms wins, so launch, lock-out and retraction count once.
- **Per-type settings in typed blocks.** These were calibrated on the first
  real take (216df089), with the video as the reference:
  - **Straights:** one burst per punch, near full extension; the defaults
    matched every jab in the checked footage.
  - **Uppercuts:** the return to guard is a second, opposite burst about
    0.43 s later and often as tall, so uppercut blocks keep the first burst and
    ignore that wrist for 0.7 s (68 → 36 on the first take).
  - **Hooks:** they peak lower, 1–2 g, at the start of the swing, so hook
    blocks use a 1.0 g threshold (lead hook 25 → 32; the checked hooks were all
    found, within 20 ms).

  Free shadowboxing has no known type, so it keeps the defaults; uppercut
  returns there can be counted twice, and review catches them.
- **Assignment.** In a typed block, punches on the expected wrist become labels
  with the block's type. Bursts on the other wrist (guard adjustments) are
  counted as `off_hand`, not labeled. In the no-punch block every burst is a
  false alarm and is counted. In free shadowboxing both wrists are labeled with
  `punch_type: null`.
- **Combos.** Bursts from both wrists are grouped into combos at the pauses
  between them. That pause length is found per block, because a fixed value
  fails: on the first combo take, pauses *inside* a 3-2 (hook, return to guard,
  then the cross) reached 1.3 s, while pauses *between* 1-1-2s went down to
  1.36 s. With 15 combos there are 14 long pauses. If they clearly stand out
  (≥1.2× the next-longest), the break sits halfway between the 14th and the
  15th longest; otherwise it's the biggest jump among pauses of at least
  0.4 s. Each combo's sequence is then walked in order, and every punch takes
  the next burst on its own wrist at least 120 ms after the previous punch.
  That means a jab's return to guard that lands before the cross is never
  taken as the following hook. Only complete combos are labeled. The block's
  count is complete combos (15 is the target), and incomplete ones are counted
  for review. Detection inside combos uses a 1.2 g threshold and a 250 ms gap,
  so a double jab stays two punches. On the first combo take (8c87402d) this
  gave 15, 15, 15 and 14 complete of 15; the missing one had no cross on
  either wrist.
- **Provenance.** Block times, the detector parameters, per-block counts and a
  checksum of the generated labels are stored in `{take}/protocol.json`.

### Quality checks

| Check | Healthy | If not |
|---|---|---|
| Typed block count | within ±2 of the reps thrown | Redo the block, or fix in review |
| No-punch block bursts | a few | Feints and sharp slips are punch-like on purpose. On the first combo take, 8 bursts came in the block's first 30 s and the cameras saw 4 of them as punch-like too. They're the hard negatives the detector must learn to reject, so keep them, and check the video only if there are many or they're spread through the block |
| Off-hand bursts in typed blocks | a few | Many → the guard hand is moving too much; ask for a still guard |

## 6. Review and inter-annotator agreement

Auto-labels are **pre-labels**. The researcher reviews every take against the
video and corrects misses, extras and the free-block types. Saving a
reviewed file protects it: Alion won't regenerate over an edited label file
unless asked explicitly (**Regenerate** then confirm).

- **Second annotator.** A coach labels a random 20% of takes
  **independently from the video, without the pre-labels**. Cohen's κ on that
  overlap measures reliability. Labeling blind also checks that the IMU
  pre-labels didn't bias the researcher's review.
- **Timing (H2c).** The IMU peak sits near full extension, not the mid-stroke
  frame the dissertation specifies. For H2c, either confirm the mid-stroke
  frame in review, or measure the constant IMU-to-mid-stroke offset on a
  reviewed subset and report it.

## 7. Splits and sizing

- **Split by fighter, never by clip or take.** The same person in train and
  test inflates every score. Hold out whole fighters.
- **Test on punches in sequence.** Single scripted punches are the easy case.
  Combos are the realistic case with known types; free shadowboxing is what
  the system meets in practice.
- **Pilot (now):** the researcher alone, ~2 takes, ≈ 400 typed punches.
  This proves the pipeline end to end. It can't support generalization claims,
  since there's no held-out fighter.
- **Benchmark (after IRB):** at least 5 fighters, both stances, ~1,500+
  punches; hold out 1–2 fighters.

### Export

**Export** on a dataset writes a de-identified copy under
`data/datasets/{dataset}/export/` (ADR-014). `manifest.json` is the one file
the training scripts read, and `takes/{take}/` holds each take's pose, sensor
data and labels under the participant's code (F-0001). There's no video,
since faces aren't blurred yet, and no name anywhere: every file is checked
for the participants' personal data before the copy replaces the previous
export.

The manifest lists every completed take with its per-block QA counts and
whether its wrists were swapped, the splits, and the takes left out
(discarded, or started but not finished). Drafts whose cameras never started
are skipped. It also carries warnings: unreviewed auto-labels, takes without
sensor data or labels, suspected wrist swaps.

- **3+ participants → `by_fighter`:** about 20% of participants are held out
  for test and 20% for validation. Whole takes go to their participant's
  split, and a seeded shuffle makes the split identical on every export.
- **1–2 participants → `pilot`:** each take is split by block (a category take
  has just one). The single-punch and no-punch blocks go to train; combos and
  free shadowboxing go to test. So the pilot asks whether punches learned in
  isolation are recognized inside combinations. The export warns that this
  can't support a claim about other people.

## 8. Where everything lives

Everything for one take is in its folder,
`data/datasets/{dataset}/takes/{take}/` (gitignored; encrypted SSD for real
athlete data):

| File | Contents |
|---|---|
| `take.json` | fighter, stance at recording time, devices with start offsets, t0, duration |
| `video/{device}.webm` + `{device}.json` | one clip per camera + its `start_offset_ms` |
| `pose/{device}.parquet` | pose per camera, on the take timeline |
| `imu.csv` | `t_ms,hand,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps` — both wrists |
| `hr.csv` | `t_ms,rr_ms,hr_bpm` — Polar H10, when worn |
| `protocol.json` | block markers, QA counts, label provenance |
| `labels.json` | pre-labels, then the reviewed labels |

## 9. Training

Training runs on the development Mac: Apple M1 Pro, 32 GB, 16-core GPU. The
models are small:

- the pose-sequence LSTM detector (`scripts/ml/train_punch_lstm.py`), on
  30-frame windows of 33 keypoints;
- the 6-class type classifier;
- an IMU model on 100 Hz six-axis windows.

Each trains in minutes; pose extraction is the heaviest step. Training reads the
export manifest. The trained weights land in `data/ml/`, where the API loads
them directly, so there's no separate deployment step. Every new model is scored against the baselines (the
heuristic detector and the Olympic-trained LSTM) on the held-out data before it
replaces anything. A cloud GPU, rented by the hour, only becomes relevant for
end-to-end video models.

## 10. Known limitations

- The per-type settings come from one fighter's first take, checked against
  video in spot strips, not frame by frame. They need re-checking as more
  fighters (and both stances) are recorded.
- Hook labels sit at the start of the swing, about 0.2–0.4 s before the fist
  crosses the centre line, so H2c timing needs a per-type offset, measured on
  reviewed labels.
- Without the wrist sensors, blocks are still marked but nothing can be
  labeled; the card warns when the sensors aren't recording.
- Browser recording is ~720p at 30 fps. That's enough for pose, but fast hooks
  blur. A high-frame-rate phone video, synced by a clap, is a possible later
  add-on (see `docs/MULTICAM_ROADMAP.md`).
