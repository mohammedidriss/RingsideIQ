"""SQLModel tables.

Pose keypoints are NOT in SQLite — they go to parquet on disk; the Session row
just holds a pointer (`pose_parquet_path`). HR samples and punch events are
tabular and live here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import PlainSerializer
from sqlmodel import Field, SQLModel


def _iso_utc(dt: datetime) -> str:
    """Serialize a datetime as ISO-8601 with an explicit UTC offset.

    Timestamps are written as ``datetime.now(UTC)`` but SQLite drops the tzinfo,
    so they come back naive. Emitting them without a zone makes the browser's
    ``new Date(...)`` read them as *local* time — showing the UTC clock value as
    if it were local, off by the viewer's offset. Assume naive == UTC and stamp
    the offset so every client localizes correctly.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


# Use on response-model datetime fields so the JSON carries a UTC offset.
UtcDatetime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str, when_used="json")]


class Stance(StrEnum):
    ORTHODOX = "orthodox"
    SOUTHPAW = "southpaw"
    SWITCH = "switch"


class SessionSourceEnum(StrEnum):
    LIVE_WEBCAM = "live_webcam"
    UPLOADED_VIDEO = "uploaded_video"
    LIVE_IPHONE = "live_iphone"
    POLAR_H10_ONLY = "polar_h10_only"
    HRV_REPLAY = "hrv_replay"


class PoseBackendEnum(StrEnum):
    MEDIAPIPE = "mediapipe"
    YOLOV8 = "yolov8"


class StudyConditionEnum(StrEnum):
    """RQ2 five-condition validation design.

    Each session in the validation study is assigned one condition.
    The condition determines which modalities are collected/analysed
    and whether AI-generated advice is available.

    - CV_ONLY:     Computer-vision punch detection only (no IMU, no HRV).
    - IMU_ONLY:    Wrist-IMU (Hykso-style) data only (no CV, no HRV).
    - HRV_ONLY:    Heart-rate variability (Polar H10) only (no CV, no IMU).
    - FUSED:       All three modalities fused + AI coaching advice.
    - COACH_ONLY:  Human coach observation only — no sensors, no AI.
                   Acts as the control condition.
    """

    CV_ONLY = "cv_only"
    IMU_ONLY = "imu_only"
    HRV_ONLY = "hrv_only"
    FUSED = "fused"
    COACH_ONLY = "coach_only"

    # ---- condition → modality gate logic ----

    @property
    def allows_cv(self) -> bool:
        return self in (self.CV_ONLY, self.FUSED)

    @property
    def allows_imu(self) -> bool:
        return self in (self.IMU_ONLY, self.FUSED)

    @property
    def allows_hrv(self) -> bool:
        return self in (self.HRV_ONLY, self.FUSED)

    @property
    def allows_ai_advice(self) -> bool:
        """coach_only condition gets NO AI-generated advice."""
        return self != self.COACH_ONLY

    @property
    def allowed_payload_mode(self) -> str:
        """The payload_mode string to use when generating advice for this condition.

        Maps each condition to the single payload slice the LLM should see.
        FUSED → "fused", CV_ONLY → "cv", etc. COACH_ONLY raises because
        advice is not allowed.
        """
        return {
            self.CV_ONLY: "cv",
            self.IMU_ONLY: "imu",
            self.HRV_ONLY: "hrv",
            self.FUSED: "fused",
        }.get(self, "fused")

    @property
    def allowed_modalities(self) -> tuple[str, ...]:
        """Human-readable list of active modalities for this condition."""
        return {
            self.CV_ONLY: ("cv",),
            self.IMU_ONLY: ("imu",),
            self.HRV_ONLY: ("hrv",),
            self.FUSED: ("cv", "imu", "hrv"),
            self.COACH_ONLY: (),
        }.get(self, ("cv", "imu", "hrv"))


class SessionStatus(StrEnum):
    PENDING = "pending"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class HandEnum(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class DetectionSourceEnum(StrEnum):
    HEURISTIC = "heuristic"
    LSTM_V1 = "lstm_v1"
    CUSTOM_ML = "custom_ml"


class SkillLevel(StrEnum):
    """A spectrum from "I just punch a bag for fitness" to working trainer."""

    RECREATIONAL = "recreational"
    AMATEUR_NOVICE = "amateur_novice"
    AMATEUR_OPEN = "amateur_open"
    AMATEUR_ELITE = "amateur_elite"
    SEMI_PRO = "semi_pro"
    PROFESSIONAL = "professional"
    COACH = "coach"


# Common boxing weight classes. Stored as a string on the row so users can
# pick one but we don't have to model every sanctioning body's variations.
WEIGHT_CLASSES = (
    "minimumweight",  # ≤105 lb
    "light_flyweight",  # ≤108 lb
    "flyweight",  # ≤112 lb
    "super_flyweight",  # ≤115 lb
    "bantamweight",  # ≤118 lb
    "super_bantamweight",  # ≤122 lb
    "featherweight",  # ≤126 lb
    "super_featherweight",  # ≤130 lb
    "lightweight",  # ≤135 lb
    "super_lightweight",  # ≤140 lb
    "welterweight",  # ≤147 lb
    "super_welterweight",  # ≤154 lb
    "middleweight",  # ≤160 lb
    "super_middleweight",  # ≤168 lb
    "light_heavyweight",  # ≤175 lb
    "cruiserweight",  # ≤200 lb
    "heavyweight",  # ≤200+ lb
)


# ----------------------------------------------------------------------
# User — authentication identity (email + hashed password).
# A user may own one or more profiles (fighter, coach, gym_manager, etc.)
# ----------------------------------------------------------------------


class UserRole(StrEnum):
    FIGHTER = "fighter"
    COACH = "coach"
    REFEREE = "referee"
    GYM_MANAGER = "gym_manager"
    ADMIN = "admin"


class User(SQLModel, table=True):
    """Authentication identity. Separate from profile entities."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=200)
    password_hash: str = Field(max_length=300)
    name: str = Field(min_length=1, max_length=120)
    role: UserRole = Field(default=UserRole.FIGHTER)
    # Link to the actual profile (fighter.id, coach.id, gym_manager.id, etc.)
    profile_id: UUID | None = Field(default=None)
    photo_path: str | None = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # The terms and conditions this person accepted, and when (ADR-016). Null until
    # they accept; requests are refused while it isn't the current version.
    terms_version: str | None = Field(default=None, max_length=40)
    terms_accepted_at: datetime | None = None


class UserCreate(SQLModel):
    email: str = Field(max_length=200)
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    role: UserRole = UserRole.FIGHTER


class UserRead(SQLModel):
    id: UUID
    email: str
    name: str
    role: UserRole
    profile_id: UUID | None = None
    photo_path: str | None = None
    is_active: bool
    created_at: UtcDatetime
    terms_version: str | None = None
    terms_accepted_at: UtcDatetime | None = None


# ----------------------------------------------------------------------
# Gym — facility where fighters train and coaches work.
# ----------------------------------------------------------------------


class Gym(SQLModel, table=True):
    """A boxing gym / training facility."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(min_length=1, max_length=160)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=160)
    specialties: str | None = Field(
        default=None,
        max_length=200,
        description="Free-form, e.g. 'boxing, MMA, strength & conditioning'",
    )
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GymCreate(SQLModel):
    name: str = Field(min_length=1, max_length=160)
    address: str | None = None
    city: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    specialties: str | None = None
    notes: str | None = None


class GymRead(SQLModel):
    id: UUID
    name: str
    address: str | None = None
    city: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    specialties: str | None = None
    notes: str | None = None
    created_at: UtcDatetime


class MembershipStatus(StrEnum):
    """Lifecycle status of a gym membership."""

    ACTIVE = "active"
    FROZEN = "frozen"  # injury, travel, financial hold
    SUSPENDED = "suspended"  # disciplinary
    TRIAL = "trial"  # trial period / drop-in
    LEFT = "left"  # departed the gym


class GymMembership(SQLModel, table=True):
    """Links fighters and coaches to a gym."""

    __tablename__ = "gym_membership"
    id: int | None = Field(default=None, primary_key=True)
    gym_id: UUID = Field(foreign_key="gym.id", index=True)
    member_id: UUID = Field(index=True)  # fighter.id or coach.id
    member_type: str = Field(max_length=10)  # "fighter" or "coach"
    status: MembershipStatus = Field(
        default=MembershipStatus.ACTIVE,
        sa_column=sa.Column(sa.String(20), nullable=False, server_default="active"),
    )
    joined_on: date = Field(default_factory=lambda: date.today())
    left_on: date | None = None  # set when status → left
    status_note: str | None = Field(
        default=None,
        max_length=300,
        description="Reason for freeze/suspension/departure",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GymMembershipRead(SQLModel):
    id: int
    gym_id: UUID
    member_id: UUID
    member_type: str
    member_name: str  # denormalised
    status: str = "active"
    joined_on: date | None = None
    left_on: date | None = None
    status_note: str | None = None
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Check-in — daily attendance tracking
# ----------------------------------------------------------------------


class CheckIn(SQLModel, table=True):
    """One row per member per gym visit."""

    __tablename__ = "check_in"
    id: int | None = Field(default=None, primary_key=True)
    gym_id: UUID = Field(foreign_key="gym.id", index=True)
    member_id: UUID = Field(index=True)  # fighter.id or coach.id
    member_type: str = Field(max_length=10)  # "fighter" or "coach"
    checked_in_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checked_out_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=200)


class CheckInRead(SQLModel):
    id: int
    gym_id: UUID
    member_id: UUID
    member_type: str
    member_name: str = ""  # denormalised
    checked_in_at: UtcDatetime
    checked_out_at: UtcDatetime | None = None
    notes: str | None = None


class GymManager(SQLModel, table=True):
    """A gym manager — can manage members for a specific gym."""

    __tablename__ = "gym_manager"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(min_length=1, max_length=120)
    photo_path: str | None = None
    email: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    gym_id: UUID = Field(foreign_key="gym.id", index=True)
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GymManagerCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    gym_id: UUID
    email: str | None = None
    phone: str | None = None


class GymManagerRead(SQLModel):
    id: UUID
    name: str
    photo_path: str | None = None
    email: str | None = None
    phone: str | None = None
    gym_id: UUID
    gym_name: str  # denormalised
    notes: str | None = None
    created_at: UtcDatetime


class Fighter(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Identity
    name: str = Field(index=True, min_length=1, max_length=120)
    nickname: str | None = Field(default=None, max_length=80)
    dob: date | None = None
    nationality: str | None = Field(default=None, max_length=80)
    sex: str | None = Field(default=None, max_length=16)  # "male" / "female" / other / None

    # Boxing stance + dominant side
    stance: Stance = Stance.ORTHODOX
    dominant_hand: HandEnum | None = None

    # Physical attributes — used for both display and CV calibration
    height_cm: float | None = Field(default=None, ge=80.0, le=250.0)
    reach_cm: float | None = Field(default=None, ge=80.0, le=260.0)
    weight_kg: float | None = Field(default=None, gt=0.0, le=400.0)
    shoulder_width_cm: float | None = Field(default=None, ge=20.0, le=80.0)

    # Competitive level
    skill_level: SkillLevel | None = None
    weight_class: str | None = Field(default=None, max_length=40)
    years_training: int | None = Field(default=None, ge=0, le=80)
    gym: str | None = Field(default=None, max_length=120)
    gym_id: UUID | None = Field(default=None, foreign_key="gym.id", index=True)
    trainer: str | None = Field(default=None, max_length=120)

    # Record
    record_wins: int = Field(default=0, ge=0)
    record_losses: int = Field(default=0, ge=0)
    record_draws: int = Field(default=0, ge=0)
    record_kos: int = Field(default=0, ge=0)

    # External identifiers (registries / governing bodies)
    boxrec_id: str | None = Field(default=None, max_length=40)
    usa_boxing_id: str | None = Field(default=None, max_length=40)

    notes: str | None = None
    photo_path: str | None = None
    bio: str | None = None  # Short paragraph for the Team tab header
    career_history: str | None = None  # Long-form free text — career arc
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FighterCreate(SQLModel):
    """Light-weight create — most fields are filled in via Edit on the profile."""

    name: str = Field(min_length=1, max_length=120)
    dob: date | None = None
    stance: Stance = Stance.ORTHODOX
    gym_id: UUID | None = None


class FighterRead(SQLModel):
    id: UUID
    name: str
    nickname: str | None = None
    dob: date | None = None
    nationality: str | None = None
    sex: str | None = None
    stance: Stance
    dominant_hand: HandEnum | None = None
    height_cm: float | None = None
    reach_cm: float | None = None
    weight_kg: float | None = None
    shoulder_width_cm: float | None = None
    skill_level: SkillLevel | None = None
    weight_class: str | None = None
    years_training: int | None = None
    gym: str | None = None
    gym_id: UUID | None = None
    trainer: str | None = None
    record_wins: int = 0
    record_losses: int = 0
    record_draws: int = 0
    record_kos: int = 0
    boxrec_id: str | None = None
    usa_boxing_id: str | None = None
    notes: str | None = None
    photo_path: str | None = None
    bio: str | None = None
    career_history: str | None = None
    created_at: UtcDatetime


class WeighIn(SQLModel, table=True):
    __tablename__ = "weigh_in"
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    weight_kg: float = Field(gt=0.0, le=400.0)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = None


class WeighInCreate(SQLModel):
    weight_kg: float = Field(gt=0.0, le=400.0)
    notes: str | None = None


class WeighInRead(SQLModel):
    id: int
    fighter_id: UUID
    weight_kg: float
    recorded_at: UtcDatetime
    notes: str | None = None


class CornerEnum(StrEnum):
    """A fighter's side in a two-fighter bout (ADR-011)."""

    RED = "red"
    BLUE = "blue"


class BoutOutcomeEnum(StrEnum):
    """Bout result by corner (or a draw)."""

    RED = "red"
    BLUE = "blue"
    DRAW = "draw"


class BellEventKindEnum(StrEnum):
    """A marker on a bout's shared round timeline."""

    ROUND_START = "round_start"
    ROUND_END = "round_end"
    BELL = "bell"


class Session(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    source: SessionSourceEnum
    status: SessionStatus = SessionStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    video_path: str | None = None
    pose_parquet_path: str | None = None
    frame_count: int = 0
    duration_ms: float = 0.0
    notes: str | None = None
    failure_reason: str | None = None
    # Pre-session resting HRV baseline (5-min recording before warmup).
    # Nullable: not all sessions have a baseline. When present, used as the
    # readiness predictor for that day's CV-derived performance score.
    baseline_rmssd_ms: float | None = None
    baseline_sdnn_ms: float | None = None
    baseline_mean_hr_bpm: float | None = None
    baseline_recorded_at: datetime | None = None
    # Round structure for the planned session — used by the in-session
    # timer + reporting. Defaults match a typical 3×3-minute pro round.
    round_count: int | None = Field(default=None, ge=1, le=24)
    round_duration_s: int | None = Field(default=None, ge=1, le=900)
    rest_duration_s: int | None = Field(default=None, ge=0, le=600)
    # Pose estimation backend used for this session (mediapipe or yolov8).
    pose_backend: PoseBackendEnum = Field(
        default=PoseBackendEnum.MEDIAPIPE,
        sa_column=sa.Column(sa.String, nullable=False, server_default="mediapipe"),
    )
    # RQ2 study condition — which modalities are active for this session.
    # NULL means the session is not part of the RQ2 validation study.
    study_condition: StudyConditionEnum | None = Field(
        default=None,
        sa_column=sa.Column(sa.String, nullable=True),
    )
    # Two-fighter bout linkage (ADR-011). NULL bout_id => an ordinary single-fighter
    # training session (today's path, unchanged). When set, this session is one
    # fighter's side of a bout, tagged red/blue. Additive per ADR-005.
    bout_id: UUID | None = Field(default=None, foreign_key="bout.id", index=True)
    corner: CornerEnum | None = Field(
        default=None,
        sa_column=sa.Column(sa.String, nullable=True),
    )


class SessionCreate(SQLModel):
    fighter_id: UUID
    source: SessionSourceEnum
    notes: str | None = None
    pose_backend: PoseBackendEnum = PoseBackendEnum.MEDIAPIPE
    study_condition: StudyConditionEnum | None = None


class SessionRead(SQLModel):
    id: UUID
    fighter_id: UUID
    source: SessionSourceEnum
    status: SessionStatus
    started_at: UtcDatetime
    ended_at: UtcDatetime | None
    video_path: str | None
    pose_parquet_path: str | None
    failure_reason: str | None = None
    frame_count: int
    duration_ms: float
    notes: str | None
    baseline_rmssd_ms: float | None = None
    baseline_sdnn_ms: float | None = None
    baseline_mean_hr_bpm: float | None = None
    baseline_recorded_at: UtcDatetime | None = None
    round_count: int | None = None
    round_duration_s: int | None = None
    rest_duration_s: int | None = None
    pose_backend: PoseBackendEnum = PoseBackendEnum.MEDIAPIPE
    study_condition: StudyConditionEnum | None = None
    bout_id: UUID | None = None
    corner: CornerEnum | None = None


class Bout(SQLModel, table=True):
    """A two-fighter fight (ADR-011). It links two single-fighter sessions — one per
    corner — and owns what they share: the round configuration and (via BellEvent)
    the bell/round timeline. The link lives on the session (`Session.bout_id` +
    `corner`), not duplicated here: a bout's participants are the two sessions whose
    `bout_id` points at it (one red, one blue)."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    label: str | None = None
    scheduled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    venue: str | None = None
    # Shared round config — authoritative for the bout; participant sessions defer.
    round_count: int = Field(default=3, ge=1, le=24)
    round_duration_s: int = Field(default=180, ge=1, le=900)
    rest_duration_s: int = Field(default=60, ge=0, le=600)
    # Result — filled in after the bout.
    winner_corner: BoutOutcomeEnum | None = Field(
        default=None,
        sa_column=sa.Column(sa.String, nullable=True),
    )
    result_method: str | None = None  # KO / TKO / decision / …
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BoutCreate(SQLModel):
    label: str | None = None
    venue: str | None = None
    round_count: int = Field(default=3, ge=1, le=24)
    round_duration_s: int = Field(default=180, ge=1, le=900)
    rest_duration_s: int = Field(default=60, ge=0, le=600)


class BoutRead(SQLModel):
    id: UUID
    label: str | None
    scheduled_at: UtcDatetime
    venue: str | None
    round_count: int
    round_duration_s: int
    rest_duration_s: int
    winner_corner: BoutOutcomeEnum | None = None
    result_method: str | None = None
    created_at: UtcDatetime


class BellEvent(SQLModel, table=True):
    """A marker on a bout's shared round timeline (ADR-011) — an offset from the bout
    T_0 (the opening bell) so both fighters' sessions align to it (ADR-006)."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    bout_id: UUID = Field(foreign_key="bout.id", index=True)
    round_index: int = Field(ge=0)
    kind: BellEventKindEnum = Field(sa_column=sa.Column(sa.String, nullable=False))
    t_ms: float


class BellEventRead(SQLModel):
    id: UUID
    bout_id: UUID
    round_index: int
    kind: BellEventKindEnum
    t_ms: float


class LeadOrRearEnum(StrEnum):
    LEAD = "lead"
    REAR = "rear"


class VelocitySourceEnum(StrEnum):
    WORLD = "world"
    IMAGE_HEURISTIC = "image_heuristic"


class PunchTypeEnum(StrEnum):
    JAB = "jab"
    CROSS = "cross"
    HOOK = "hook"
    UPPERCUT = "uppercut"


class PunchEventRow(SQLModel, table=True):
    __tablename__ = "punch_event"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    t_ms: float
    hand: HandEnum
    lead_or_rear: LeadOrRearEnum | None = None
    velocity_ms: float
    velocity_source: VelocitySourceEnum = VelocitySourceEnum.IMAGE_HEURISTIC
    punch_type: PunchTypeEnum | None = None
    detected_by: DetectionSourceEnum
    confidence: float


class PunchEventRead(SQLModel):
    session_id: UUID
    t_ms: float
    hand: HandEnum
    lead_or_rear: LeadOrRearEnum | None = None
    velocity_ms: float
    velocity_source: VelocitySourceEnum = VelocitySourceEnum.IMAGE_HEURISTIC
    punch_type: PunchTypeEnum | None = None
    detected_by: DetectionSourceEnum
    confidence: float


class HRSampleRow(SQLModel, table=True):
    __tablename__ = "hr_sample"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    t_ms: float
    rr_ms: float
    hr_bpm: float


class HRSampleRead(SQLModel):
    session_id: UUID
    t_ms: float
    rr_ms: float
    hr_bpm: float


# ----------------------------------------------------------------------
# IMU samples — wrist/glove inertial sensor (accelerometer + gyroscope).
# Until real Hykso/Corner trackers arrive, the rows are populated by the
# synthetic generator, the phone-IMU bridge, or a CSV upload.
# ----------------------------------------------------------------------


class IMUSampleRow(SQLModel, table=True):
    __tablename__ = "imu_sample"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    t_ms: float
    # Accelerometer (g). Magnitude is the most useful quantity for punch
    # detection; component axes preserved for downstream classifiers.
    ax_g: float
    ay_g: float
    az_g: float
    # Gyroscope (deg/s). Optional but kept symmetric so a single-row
    # contract works for both wrist IMUs and 6-DOF glove sensors.
    gx_dps: float = 0.0
    gy_dps: float = 0.0
    gz_dps: float = 0.0
    # Which hand the sensor was on, when known.
    hand: HandEnum | None = None


class IMUSampleRead(SQLModel):
    session_id: UUID
    t_ms: float
    ax_g: float
    ay_g: float
    az_g: float
    gx_dps: float = 0.0
    gy_dps: float = 0.0
    gz_dps: float = 0.0
    hand: HandEnum | None = None


# ----------------------------------------------------------------------
# Saved round-structure plans — small reusable presets the fighter can
# apply to a session in one click ("3×3 + 1 (pro spar)" etc.). Server
# caps the count at MAX_ROUND_PLANS to keep the UI list short.
# ----------------------------------------------------------------------

MAX_ROUND_PLANS = 3


class RoundPlanRow(SQLModel, table=True):
    __tablename__ = "round_plan"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(min_length=1, max_length=60)
    round_count: int = Field(ge=1, le=24)
    round_duration_s: int = Field(ge=1, le=900)
    rest_duration_s: int = Field(ge=0, le=600)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoundPlanCreate(SQLModel):
    name: str = Field(min_length=1, max_length=60)
    round_count: int = Field(ge=1, le=24)
    round_duration_s: int = Field(ge=1, le=900)
    rest_duration_s: int = Field(ge=0, le=600)


class RoundPlanRead(SQLModel):
    id: int
    name: str
    round_count: int
    round_duration_s: int
    rest_duration_s: int
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Consensus events — output of live + offline detector reconciliation.
# Live = `HeuristicPunchDetector` (punch_event rows). Offline = whatever
# second-pass model is registered (LSTM if available, else stricter
# heuristic). Each row records which sources voted for the event so
# downstream consumers can filter (e.g. RQ1 advice prefers `consensus`).
# ----------------------------------------------------------------------


class ConsensusKindEnum(StrEnum):
    CONSENSUS = "consensus"
    LIVE_ONLY = "live_only"
    OFFLINE_ONLY = "offline_only"


class ConsensusEventRow(SQLModel, table=True):
    __tablename__ = "consensus_event"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    t_ms: float
    hand: HandEnum
    velocity_ms: float
    punch_type: PunchTypeEnum | None = None
    confidence: float
    kind: ConsensusKindEnum
    # Comma-separated source labels (e.g. "live,offline" or "live" or
    # "offline,lstm_v1"). Free-form so we can plug in new detectors
    # without a schema change.
    sources: str
    second_pass_name: str  # name of the offline detector that produced this row's pass
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsensusEventRead(SQLModel):
    session_id: UUID
    t_ms: float
    hand: HandEnum
    velocity_ms: float
    punch_type: PunchTypeEnum | None = None
    confidence: float
    kind: ConsensusKindEnum
    sources: str
    second_pass_name: str


# ----------------------------------------------------------------------
# RQ1 study tables — advice cache + rater scores.
# Cache fixes the "same session generates different advice each click"
# problem: every (session, payload_mode, prompt_version) tuple is
# generated once and reused for every rater. Ratings live on the server
# so a study can pool data across raters and machines.
# ----------------------------------------------------------------------


class PayloadModeEnum(StrEnum):
    CV = "cv"
    HRV = "hrv"
    IMU = "imu"
    FUSED = "fused"


class CoachAdviceCacheRow(SQLModel, table=True):
    __tablename__ = "coach_advice_cache"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    payload_mode: PayloadModeEnum
    # Bumping `prompt_version` invalidates the cache; the route checks
    # it against `coach.PROMPT_VERSION` and regenerates on mismatch.
    prompt_version: str = "v1"
    summary: str
    # JSON-encoded list[str] for portability across SQLite + Postgres.
    action_items_json: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RaterScoreRow(SQLModel, table=True):
    __tablename__ = "rq1_rating"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    payload_mode: PayloadModeEnum
    rater_id: str = Field(min_length=1, max_length=80, index=True)
    # 4-criterion Likert; one row per (rater, session, mode, criterion).
    criterion: str = Field(min_length=1, max_length=40)
    score: int = Field(ge=1, le=5)
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RaterScoreRead(SQLModel):
    session_id: UUID
    payload_mode: PayloadModeEnum
    rater_id: str
    criterion: str
    score: int
    notes: str | None = None
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Coach + Referee profiles
# ----------------------------------------------------------------------


class CoachingLevel(StrEnum):
    AMATEUR = "amateur"
    PROFESSIONAL = "professional"
    BOTH = "both"


class Coach(SQLModel, table=True):
    """Coach profile — manages fighters, adds session observations."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Identity
    name: str = Field(min_length=1, max_length=120)
    photo_path: str | None = None
    dob: date | None = None
    nationality: str | None = Field(default=None, max_length=80)
    sex: str | None = Field(default=None, max_length=16)
    # Contact
    email: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    # Coaching context
    gym: str | None = Field(default=None, max_length=120)
    gym_id: UUID | None = Field(default=None, foreign_key="gym.id", index=True)
    specialties: str | None = Field(
        default=None,
        max_length=200,
        description="Free-form list, e.g. 'head movement, defense, conditioning'",
    )
    coaching_level: CoachingLevel | None = None
    years_experience: int | None = Field(default=None, ge=0, le=80)
    # Credentials
    certifications: str | None = Field(
        default=None,
        max_length=300,
        description="Comma-separated, e.g. 'USA Boxing Level 2, AIBA 1-Star'",
    )
    license_number: str | None = Field(default=None, max_length=80)
    license_expiry: date | None = None
    # Other
    languages: str | None = Field(default=None, max_length=200, description="Comma-separated")
    notable_fighters: str | None = Field(
        default=None,
        max_length=400,
        description="Comma-separated names of notable fighters trained",
    )
    bio: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CoachCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    gym: str | None = None
    gym_id: UUID | None = None
    specialties: str | None = None
    years_experience: int | None = None
    bio: str | None = None


class CoachRead(SQLModel):
    id: UUID
    name: str
    photo_path: str | None = None
    dob: date | None = None
    nationality: str | None = None
    sex: str | None = None
    email: str | None = None
    phone: str | None = None
    gym: str | None = None
    gym_id: UUID | None = None
    specialties: str | None = None
    coaching_level: CoachingLevel | None = None
    years_experience: int | None = None
    certifications: str | None = None
    license_number: str | None = None
    license_expiry: date | None = None
    languages: str | None = None
    notable_fighters: str | None = None
    bio: str | None = None
    notes: str | None = None
    created_at: UtcDatetime


class RefereeCertLevel(StrEnum):
    LOCAL = "local"
    REGIONAL = "regional"
    NATIONAL = "national"
    INTERNATIONAL = "international"


class Referee(SQLModel, table=True):
    """Referee profile — sanctioned official who oversees bouts."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Identity
    name: str = Field(min_length=1, max_length=120)
    photo_path: str | None = None
    dob: date | None = None
    nationality: str | None = Field(default=None, max_length=80)
    sex: str | None = Field(default=None, max_length=16)
    # Contact
    email: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    # Credentials
    license_number: str | None = Field(default=None, max_length=80)
    sanctioning_body: str | None = Field(
        default=None,
        max_length=120,
        description="e.g. 'USA Boxing', 'WBA', 'BBBofC'",
    )
    certification_level: RefereeCertLevel | None = None
    license_expiry: date | None = None
    years_officiating: int | None = Field(default=None, ge=0, le=80)
    # Other
    languages: str | None = Field(default=None, max_length=200)
    notable_bouts: str | None = Field(
        default=None,
        max_length=400,
        description="Comma-separated, e.g. 'Mayweather vs Pacquiao 2015'",
    )
    bio: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RefereeCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    license_number: str | None = None
    sanctioning_body: str | None = None
    license_expiry: date | None = None
    bio: str | None = None


class RefereeRead(SQLModel):
    id: UUID
    name: str
    photo_path: str | None = None
    dob: date | None = None
    nationality: str | None = None
    sex: str | None = None
    email: str | None = None
    phone: str | None = None
    license_number: str | None = None
    sanctioning_body: str | None = None
    certification_level: RefereeCertLevel | None = None
    license_expiry: date | None = None
    years_officiating: int | None = None
    languages: str | None = None
    notable_bouts: str | None = None
    bio: str | None = None
    notes: str | None = None
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Medical records (fighters only)
# ----------------------------------------------------------------------


class AllergySeverity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    ANAPHYLACTIC = "anaphylactic"


class ConditionStatus(StrEnum):
    ACTIVE = "active"
    MANAGED = "managed"
    RECOVERED = "recovered"


class MedicalRecord(SQLModel, table=True):
    """One-to-one medical record per fighter. Free-form fields are intentionally
    optional — the schema is wide so the dashboard can surface what's filled
    without forcing the coach to enter everything up front."""

    __tablename__ = "medical_record"
    fighter_id: UUID = Field(foreign_key="fighter.id", primary_key=True, index=True)
    blood_type: str | None = Field(default=None, max_length=4)  # "A+", "O-", etc.
    last_clearance_date: date | None = None
    clearing_physician: str | None = Field(default=None, max_length=120)
    primary_physician: str | None = Field(default=None, max_length=120)
    primary_physician_phone: str | None = Field(default=None, max_length=40)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_relation: str | None = Field(default=None, max_length=80)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)
    insurance_provider: str | None = Field(default=None, max_length=120)
    insurance_policy: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MedicalRecordRead(SQLModel):
    fighter_id: UUID
    blood_type: str | None = None
    last_clearance_date: date | None = None
    clearing_physician: str | None = None
    primary_physician: str | None = None
    primary_physician_phone: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_relation: str | None = None
    emergency_contact_phone: str | None = None
    insurance_provider: str | None = None
    insurance_policy: str | None = None
    notes: str | None = None
    updated_at: UtcDatetime


class Allergy(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    substance: str = Field(min_length=1, max_length=120)
    severity: AllergySeverity = AllergySeverity.MILD
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AllergyCreate(SQLModel):
    substance: str = Field(min_length=1, max_length=120)
    severity: AllergySeverity = AllergySeverity.MILD
    notes: str | None = None


class AllergyRead(SQLModel):
    id: int
    fighter_id: UUID
    substance: str
    severity: AllergySeverity
    notes: str | None = None
    created_at: UtcDatetime


class Medication(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    name: str = Field(min_length=1, max_length=120)
    dose: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=80)  # "daily", "as needed"
    started_on: date | None = None
    prescribed_by: str | None = Field(default=None, max_length=120)
    is_active: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MedicationCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    dose: str | None = None
    frequency: str | None = None
    started_on: date | None = None
    prescribed_by: str | None = None
    is_active: bool = True
    notes: str | None = None


class MedicationRead(SQLModel):
    id: int
    fighter_id: UUID
    name: str
    dose: str | None = None
    frequency: str | None = None
    started_on: date | None = None
    prescribed_by: str | None = None
    is_active: bool
    notes: str | None = None
    created_at: UtcDatetime


class MedicalCondition(SQLModel, table=True):
    __tablename__ = "medical_condition"
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    name: str = Field(min_length=1, max_length=160)
    diagnosed_on: date | None = None
    status: ConditionStatus = ConditionStatus.ACTIVE
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MedicalConditionCreate(SQLModel):
    name: str = Field(min_length=1, max_length=160)
    diagnosed_on: date | None = None
    status: ConditionStatus = ConditionStatus.ACTIVE
    notes: str | None = None


class MedicalConditionRead(SQLModel):
    id: int
    fighter_id: UUID
    name: str
    diagnosed_on: date | None = None
    status: ConditionStatus
    notes: str | None = None
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Fighter team / titles / sponsors
# ----------------------------------------------------------------------


class TitleStatus(StrEnum):
    """Lifecycle of a championship title."""

    ACTIVE = "active"  # currently held
    LOST = "lost"
    VACATED = "vacated"
    RETIRED = "retired"


class FighterTitle(SQLModel, table=True):
    __tablename__ = "fighter_title"
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    name: str = Field(min_length=1, max_length=160)  # "WBC heavyweight"
    organization: str | None = Field(default=None, max_length=80)  # WBC / WBA / IBF / ...
    weight_class: str | None = Field(default=None, max_length=40)
    won_on: date | None = None
    lost_on: date | None = None
    status: TitleStatus = TitleStatus.ACTIVE
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FighterTitleCreate(SQLModel):
    name: str = Field(min_length=1, max_length=160)
    organization: str | None = None
    weight_class: str | None = None
    won_on: date | None = None
    lost_on: date | None = None
    status: TitleStatus = TitleStatus.ACTIVE
    notes: str | None = None


class FighterTitleRead(SQLModel):
    id: int
    fighter_id: UUID
    name: str
    organization: str | None = None
    weight_class: str | None = None
    won_on: date | None = None
    lost_on: date | None = None
    status: TitleStatus
    notes: str | None = None
    created_at: UtcDatetime


class FighterSponsor(SQLModel, table=True):
    __tablename__ = "fighter_sponsor"
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    name: str = Field(min_length=1, max_length=160)
    started_on: date | None = None
    ended_on: date | None = None  # None = current
    website: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FighterSponsorCreate(SQLModel):
    name: str = Field(min_length=1, max_length=160)
    started_on: date | None = None
    ended_on: date | None = None
    website: str | None = None
    notes: str | None = None


class FighterSponsorRead(SQLModel):
    id: int
    fighter_id: UUID
    name: str
    started_on: date | None = None
    ended_on: date | None = None
    website: str | None = None
    notes: str | None = None
    created_at: UtcDatetime


class CoachRole(StrEnum):
    """Common coach roles. Free-text 'role' on the assignment lets us add new
    ones without a migration; the enum just guides the UI."""

    HEAD_COACH = "head_coach"
    STRIKING = "striking"
    STRENGTH = "strength"
    CONDITIONING = "conditioning"
    NUTRITION = "nutrition"
    CUTMAN = "cutman"
    MENTAL = "mental"
    OTHER = "other"


class CoachAssignment(SQLModel, table=True):
    """Many-to-many: a fighter can have many coaches concurrently (head,
    strength, cutman, …); a coach can train many fighters."""

    __tablename__ = "coach_assignment"
    id: int | None = Field(default=None, primary_key=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    coach_id: UUID = Field(foreign_key="coach.id", index=True)
    role: CoachRole = CoachRole.HEAD_COACH
    started_on: date | None = None
    ended_on: date | None = None  # None = current
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CoachAssignmentCreate(SQLModel):
    coach_id: UUID
    role: CoachRole = CoachRole.HEAD_COACH
    started_on: date | None = None
    ended_on: date | None = None
    notes: str | None = None


class CoachAssignmentRead(SQLModel):
    id: int
    fighter_id: UUID
    coach_id: UUID
    coach_name: str  # denormalised for the UI; not a DB column
    coach_photo_path: str | None = None  # ditto
    role: CoachRole
    started_on: date | None = None
    ended_on: date | None = None
    notes: str | None = None
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Coach notes — free-form observations a coach writes about a fighter
# outside of any particular session.
# ----------------------------------------------------------------------


class CoachNote(SQLModel, table=True):
    """A timestamped note written by a coach about a fighter.

    Lives outside the session model so coaches can record observations
    that span multiple sessions (e.g. "guard has been dropping for the
    last three sparring rounds").
    """

    __tablename__ = "coach_note"
    id: int | None = Field(default=None, primary_key=True)
    coach_id: UUID = Field(foreign_key="coach.id", index=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    content: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CoachNoteCreate(SQLModel):
    content: str = Field(min_length=1)


class CoachNoteRead(SQLModel):
    id: int
    coach_id: UUID
    fighter_id: UUID
    coach_name: str  # denormalised for the UI
    coach_photo_path: str | None = None
    content: str
    created_at: UtcDatetime


# ----------------------------------------------------------------------
# Session attachments — arbitrary files (extra videos, sparring photos,
# coach notes PDFs, etc.) hung off a session for reference.
# ----------------------------------------------------------------------


class AttachmentKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class SessionAttachment(SQLModel, table=True):
    __tablename__ = "session_attachment"
    id: int | None = Field(default=None, primary_key=True)
    session_id: UUID = Field(foreign_key="session.id", index=True)
    filename: str = Field(min_length=1, max_length=255)
    path: str = Field(max_length=400)
    mime_type: str | None = Field(default=None, max_length=120)
    size_bytes: int = Field(default=0, ge=0)
    kind: AttachmentKind = AttachmentKind.OTHER
    notes: str | None = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionAttachmentRead(SQLModel):
    id: int
    session_id: UUID
    filename: str
    path: str
    mime_type: str | None = None
    size_bytes: int
    kind: AttachmentKind
    notes: str | None = None
    uploaded_at: UtcDatetime


# ---------------------------------------------------------------------------
# Datasets (ADR-013) — a recording system separate from training sessions.
# These tables only index take folders (data/datasets/…) and enforce consent;
# the recorded data itself lives in the files.
# ---------------------------------------------------------------------------


class ConsentEnum(StrEnum):
    """A dataset participant's consent. Only `self` (the researcher recording
    himself) and `irb_signed` may be recorded (ADR-002)."""

    SELF = "self"
    IRB_SIGNED = "irb_signed"
    PENDING = "pending"
    WITHDRAWN = "withdrawn"

    @property
    def may_record(self) -> bool:
        return self in (ConsentEnum.SELF, ConsentEnum.IRB_SIGNED)


class TakeStatusEnum(StrEnum):
    RECORDING = "recording"  # created; capture may be running
    COMPLETED = "completed"  # stopped with video saved
    DISCARDED = "discarded"  # kept on disk, excluded from the dataset


class Dataset(SQLModel, table=True):
    __tablename__ = "dataset"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    protocol: str | None = Field(default=None, max_length=60)  # protocol key, e.g. "rq2-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DatasetCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    protocol: str | None = Field(default=None, max_length=60)


class DatasetRead(SQLModel):
    id: UUID
    name: str
    description: str | None = None
    protocol: str | None = None
    created_at: UtcDatetime


PARTICIPANT_CODE_PREFIX = "F-"


def participant_code(n: int) -> str:
    """The n-th participant's code in a dataset: F-0001, F-0002, …"""
    return f"{PARTICIPANT_CODE_PREFIX}{n:04d}"


class DatasetParticipant(SQLModel, table=True):
    __tablename__ = "dataset_participant"
    __table_args__ = (
        sa.UniqueConstraint("dataset_id", "fighter_id"),
        sa.Index("ix_dataset_participant_code", "dataset_id", "code", unique=True),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    dataset_id: UUID = Field(foreign_key="dataset.id", index=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    # The participant's code in this dataset (ADR-014): everything the dataset stores
    # or exports names them by this, never by name. Assigned when they're added.
    code: str | None = Field(default=None, max_length=12)
    consent: ConsentEnum = Field(
        default=ConsentEnum.PENDING, sa_column=sa.Column(sa.String, nullable=False)
    )
    consent_date: date | None = None
    irb_ref: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DatasetParticipantIn(SQLModel):
    fighter_id: UUID
    consent: ConsentEnum = ConsentEnum.PENDING
    consent_date: date | None = None
    irb_ref: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class DatasetTake(SQLModel, table=True):
    __tablename__ = "dataset_take"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    dataset_id: UUID = Field(foreign_key="dataset.id", index=True)
    fighter_id: UUID = Field(foreign_key="fighter.id", index=True)
    status: TakeStatusEnum = Field(
        default=TakeStatusEnum.RECORDING, sa_column=sa.Column(sa.String, nullable=False)
    )
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    duration_ms: float = Field(default=0.0, ge=0)
    notes: str | None = None
