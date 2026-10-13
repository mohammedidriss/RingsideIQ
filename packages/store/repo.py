"""Typed repositories. Thin wrappers over SQLModel; testable in isolation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session as DBSession
from sqlmodel import select

from store.models import (
    PARTICIPANT_CODE_PREFIX,
    Allergy,
    AllergyCreate,
    Bout,
    BoutCreate,
    BoutOutcomeEnum,
    CheckIn,
    Coach,
    CoachAssignment,
    CoachAssignmentCreate,
    CoachCreate,
    CoachNote,
    ConsensusEventRow,
    ConsentEnum,
    CornerEnum,
    Dataset,
    DatasetCreate,
    DatasetParticipant,
    DatasetParticipantIn,
    DatasetTake,
    Fighter,
    FighterCreate,
    FighterSponsor,
    FighterSponsorCreate,
    FighterTitle,
    FighterTitleCreate,
    Gym,
    GymCreate,
    GymManager,
    GymManagerCreate,
    GymMembership,
    HRSampleRow,
    IMUSampleRow,
    MedicalCondition,
    MedicalConditionCreate,
    MedicalRecord,
    Medication,
    MedicationCreate,
    PunchEventRow,
    Referee,
    RefereeCreate,
    Session,
    SessionCreate,
    SessionStatus,
    Stance,
    TakeStatusEnum,
    User,
    UserCreate,
    WeighIn,
    WeighInCreate,
    participant_code,
)


class FighterRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: FighterCreate) -> Fighter:
        fighter = Fighter(**data.model_dump())
        self._session.add(fighter)
        self._session.commit()
        self._session.refresh(fighter)
        return fighter

    def get(self, fighter_id: UUID) -> Fighter | None:
        return self._session.get(Fighter, fighter_id)

    def list_all(self) -> list[Fighter]:
        return list(self._session.exec(select(Fighter)).all())

    def list_for_gym(self, gym_id: UUID) -> list[Fighter]:
        stmt = select(Fighter).where(Fighter.gym_id == gym_id)
        return list(self._session.exec(stmt).all())

    def update(self, fighter_id: UUID, patch: dict[str, object]) -> Fighter | None:
        """Apply a partial patch to a fighter row. Unknown keys are ignored.

        Stance / SkillLevel / HandEnum strings are coerced to enum members so
        the API doesn't have to know about SQLModel internals.
        """
        from store.models import HandEnum, SkillLevel

        fighter = self.get(fighter_id)
        if fighter is None:
            return None
        for key, value in patch.items():
            if value is None:
                # Allow nulling out optional fields except `stance`/`name`.
                if key in ("stance", "name"):
                    continue
            if not hasattr(fighter, key):
                continue
            if key == "stance" and isinstance(value, str):
                value = Stance(value)
            elif key == "dominant_hand" and isinstance(value, str):
                value = HandEnum(value)
            elif key == "skill_level" and isinstance(value, str):
                value = SkillLevel(value)
            setattr(fighter, key, value)
        self._session.add(fighter)
        self._session.commit()
        self._session.refresh(fighter)
        return fighter

    def delete(self, fighter_id: UUID) -> bool:
        fighter = self.get(fighter_id)
        if fighter is None:
            return False
        # Cascade through sessions (which themselves cascade to their child rows).
        sessions = SessionRepo(self._session).list_for_fighter(fighter_id)
        for s in sessions:
            SessionRepo(self._session).delete(s.id)
        self._session.delete(fighter)
        self._session.commit()
        return True


class SessionRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: SessionCreate) -> Session:
        row = Session(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, session_id: UUID) -> Session | None:
        return self._session.get(Session, session_id)

    def list_for_fighter(self, fighter_id: UUID) -> list[Session]:
        stmt = select(Session).where(Session.fighter_id == fighter_id)
        return list(self._session.exec(stmt).all())

    def list_all(self) -> list[Session]:
        return list(self._session.exec(select(Session)).all())

    def update_status(
        self,
        session_id: UUID,
        status: SessionStatus,
        end: bool = False,
        failure_reason: str | None = None,
    ) -> Session | None:
        row = self.get(session_id)
        if row is None:
            return None
        row.status = status
        if end:
            row.ended_at = datetime.now(UTC)
        if failure_reason is not None:
            row.failure_reason = failure_reason
        elif status != SessionStatus.FAILED:
            row.failure_reason = None
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, session_id: UUID) -> bool:
        row = self.get(session_id)
        if row is None:
            return False
        # Cascade: drop child rows first (no FK CASCADE in SQLite by default).
        from sqlmodel import delete as sqlmodel_delete

        self._session.exec(
            sqlmodel_delete(PunchEventRow).where(PunchEventRow.session_id == session_id)  # type: ignore[arg-type]
        )
        self._session.exec(
            sqlmodel_delete(HRSampleRow).where(HRSampleRow.session_id == session_id)  # type: ignore[arg-type]
        )
        self._session.exec(
            sqlmodel_delete(IMUSampleRow).where(IMUSampleRow.session_id == session_id)  # type: ignore[arg-type]
        )
        self._session.exec(
            sqlmodel_delete(ConsensusEventRow).where(ConsensusEventRow.session_id == session_id)  # type: ignore[arg-type]
        )
        self._session.delete(row)
        self._session.commit()
        return True

    def attach_artifacts(
        self,
        session_id: UUID,
        *,
        video_path: str | None = None,
        pose_parquet_path: str | None = None,
        frame_count: int | None = None,
        duration_ms: float | None = None,
    ) -> Session | None:
        row = self.get(session_id)
        if row is None:
            return None
        if video_path is not None:
            row.video_path = video_path
        if pose_parquet_path is not None:
            row.pose_parquet_path = pose_parquet_path
        if frame_count is not None:
            row.frame_count = frame_count
        if duration_ms is not None:
            row.duration_ms = duration_ms
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def attach_baseline(
        self,
        session_id: UUID,
        *,
        rmssd_ms: float,
        sdnn_ms: float,
        mean_hr_bpm: float,
    ) -> Session | None:
        from datetime import UTC, datetime

        row = self.get(session_id)
        if row is None:
            return None
        row.baseline_rmssd_ms = rmssd_ms
        row.baseline_sdnn_ms = sdnn_ms
        row.baseline_mean_hr_bpm = mean_hr_bpm
        row.baseline_recorded_at = datetime.now(UTC)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row


class BoutRepo:
    """Two-fighter bouts (ADR-011). A bout owns the shared round config + result; the
    two participants are the sessions whose `bout_id` points here (one red, one blue).
    Corner assignment lives here so the one-red-one-blue, two-max, one-bout-per-session
    rules are enforced in one place."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: BoutCreate) -> Bout:
        row = Bout(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, bout_id: UUID) -> Bout | None:
        return self._session.get(Bout, bout_id)

    def list_all(self) -> list[Bout]:
        stmt = select(Bout).order_by(Bout.scheduled_at)  # type: ignore[arg-type]
        return list(self._session.exec(stmt).all())

    def participants(self, bout_id: UUID) -> list[Session]:
        stmt = select(Session).where(Session.bout_id == bout_id)
        return list(self._session.exec(stmt).all())

    def assign_corner(self, bout_id: UUID, session_id: UUID, corner: CornerEnum) -> Session | None:
        """Attach a session to a bout in a corner. Returns the updated session, None if
        the bout doesn't exist, or raises ValueError on a rule violation (unknown
        session, corner taken, session already in another bout, or bout already full)."""
        if self.get(bout_id) is None:
            return None
        sess = self._session.get(Session, session_id)
        if sess is None:
            raise ValueError("session not found")
        if sess.bout_id is not None and sess.bout_id != bout_id:
            raise ValueError("session already belongs to another bout")
        current = self.participants(bout_id)
        for p in current:
            if p.corner == corner and p.id != session_id:
                raise ValueError(f"{corner.value} corner is already taken")
        if len([p for p in current if p.id != session_id]) >= 2:
            raise ValueError("bout already has two participants")
        sess.bout_id = bout_id
        sess.corner = corner
        self._session.add(sess)
        self._session.commit()
        self._session.refresh(sess)
        return sess

    def remove_participant(self, session_id: UUID) -> Session | None:
        sess = self._session.get(Session, session_id)
        if sess is None:
            return None
        sess.bout_id = None
        sess.corner = None
        self._session.add(sess)
        self._session.commit()
        self._session.refresh(sess)
        return sess

    def set_result(
        self,
        bout_id: UUID,
        winner_corner: BoutOutcomeEnum | None,
        result_method: str | None,
    ) -> Bout | None:
        bout = self.get(bout_id)
        if bout is None:
            return None
        bout.winner_corner = winner_corner
        bout.result_method = result_method
        self._session.add(bout)
        self._session.commit()
        self._session.refresh(bout)
        return bout


class PunchEventRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def add_many(self, events: list[PunchEventRow]) -> int:
        for e in events:
            self._session.add(e)
        self._session.commit()
        return len(events)

    def list_for_session(self, session_id: UUID) -> list[PunchEventRow]:
        stmt = (
            select(PunchEventRow)
            .where(PunchEventRow.session_id == session_id)
            .order_by(PunchEventRow.t_ms)  # type: ignore[arg-type]
        )
        return list(self._session.exec(stmt).all())

    def count_for_session(self, session_id: UUID) -> int:
        stmt = select(PunchEventRow).where(PunchEventRow.session_id == session_id)
        return len(list(self._session.exec(stmt).all()))


class WeighInRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, fighter_id: UUID, data: WeighInCreate) -> WeighIn:
        row = WeighIn(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        # Mirror the latest weigh-in onto the Fighter row so the profile card
        # can read current weight without a separate query.
        f = self._session.get(Fighter, fighter_id)
        if f is not None:
            f.weight_kg = data.weight_kg
            self._session.add(f)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_for_fighter(self, fighter_id: UUID) -> list[WeighIn]:
        stmt = (
            select(WeighIn).where(WeighIn.fighter_id == fighter_id).order_by(WeighIn.recorded_at)  # type: ignore[arg-type]
        )
        return list(self._session.exec(stmt).all())

    def delete(self, weigh_in_id: int) -> bool:
        row = self._session.get(WeighIn, weigh_in_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


# ----------------------------------------------------------------------
# Coach + Referee
# ----------------------------------------------------------------------


class CoachRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: CoachCreate) -> Coach:
        row = Coach(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, coach_id: UUID) -> Coach | None:
        return self._session.get(Coach, coach_id)

    def list_all(self) -> list[Coach]:
        return list(self._session.exec(select(Coach).order_by(Coach.name)).all())

    def list_for_gym(self, gym_id: UUID) -> list[Coach]:
        stmt = select(Coach).where(Coach.gym_id == gym_id).order_by(Coach.name)
        return list(self._session.exec(stmt).all())

    def update(self, coach_id: UUID, patch: dict[str, object]) -> Coach | None:
        row = self.get(coach_id)
        if row is None:
            return None
        for k, v in patch.items():
            if hasattr(row, k):
                setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, coach_id: UUID) -> bool:
        row = self.get(coach_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


class GymRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: GymCreate) -> Gym:
        row = Gym(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, gym_id: UUID) -> Gym | None:
        return self._session.get(Gym, gym_id)

    def list_all(self) -> list[Gym]:
        return list(self._session.exec(select(Gym).order_by(Gym.name)).all())

    def update(self, gym_id: UUID, patch: dict[str, object]) -> Gym | None:
        row = self.get(gym_id)
        if row is None:
            return None
        for k, v in patch.items():
            if hasattr(row, k):
                setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, gym_id: UUID) -> bool:
        row = self.get(gym_id)
        if row is None:
            return False
        # Remove memberships first
        from sqlmodel import delete as sqlmodel_delete

        self._session.exec(
            sqlmodel_delete(GymMembership).where(GymMembership.gym_id == gym_id)  # type: ignore[arg-type]
        )
        self._session.delete(row)
        self._session.commit()
        return True

    def add_member(self, gym_id: UUID, member_id: UUID, member_type: str) -> GymMembership:
        from store.models import MembershipStatus

        row = GymMembership(
            gym_id=gym_id,
            member_id=member_id,
            member_type=member_type,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_members(
        self,
        gym_id: UUID,
        *,
        include_left: bool = False,
    ) -> list[tuple[GymMembership, str]]:
        """Returns (membership, member_name) tuples.

        By default only active/frozen/suspended/trial members are returned.
        Pass include_left=True to include departed members.
        """
        from store.models import MembershipStatus

        # Fighters
        fighter_stmt = (
            select(GymMembership, Fighter.name)
            .join(Fighter, Fighter.id == GymMembership.member_id)  # type: ignore[arg-type]
            .where(
                GymMembership.gym_id == gym_id,
                GymMembership.member_type == "fighter",
            )
        )
        # Coaches
        coach_stmt = (
            select(GymMembership, Coach.name)
            .join(Coach, Coach.id == GymMembership.member_id)  # type: ignore[arg-type]
            .where(
                GymMembership.gym_id == gym_id,
                GymMembership.member_type == "coach",
            )
        )

        if not include_left:
            fighter_stmt = fighter_stmt.where(GymMembership.status != MembershipStatus.LEFT)
            coach_stmt = coach_stmt.where(GymMembership.status != MembershipStatus.LEFT)

        rows = list(self._session.exec(fighter_stmt).all()) + list(
            self._session.exec(coach_stmt).all()
        )
        rows.sort(key=lambda r: (r[0].member_type, r[1]))
        return rows

    def update_membership_status(
        self,
        gym_id: UUID,
        membership_id: int,
        status: str,
        note: str | None = None,
    ) -> GymMembership | None:
        """Change membership status (active, frozen, suspended, trial, left)."""
        from datetime import date as _date

        from store.models import MembershipStatus

        row = self._session.get(GymMembership, membership_id)
        if row is None or row.gym_id != gym_id:
            return None
        row.status = MembershipStatus(status)
        row.status_note = note
        if status == "left":
            row.left_on = _date.today()
        elif row.left_on is not None and status != "left":
            # Reactivating — clear departure date
            row.left_on = None
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def remove_member(self, gym_id: UUID, membership_id: int) -> bool:
        """Soft-remove: sets status to 'left' and records departure date."""
        result = self.update_membership_status(gym_id, membership_id, "left")
        return result is not None


class RefereeRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: RefereeCreate) -> Referee:
        row = Referee(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, referee_id: UUID) -> Referee | None:
        return self._session.get(Referee, referee_id)

    def list_all(self) -> list[Referee]:
        return list(self._session.exec(select(Referee).order_by(Referee.name)).all())

    def update(self, referee_id: UUID, patch: dict[str, object]) -> Referee | None:
        row = self.get(referee_id)
        if row is None:
            return None
        for k, v in patch.items():
            if hasattr(row, k):
                setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, referee_id: UUID) -> bool:
        row = self.get(referee_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


# ----------------------------------------------------------------------
# Medical record + sub-tables
# ----------------------------------------------------------------------


class MedicalRepo:
    """Per-fighter medical record + allergies + medications + conditions."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def get_record(self, fighter_id: UUID) -> MedicalRecord | None:
        return self._session.get(MedicalRecord, fighter_id)

    def upsert_record(self, fighter_id: UUID, patch: dict[str, object]) -> MedicalRecord:
        row = self.get_record(fighter_id)
        if row is None:
            row = MedicalRecord(fighter_id=fighter_id)
        for k, v in patch.items():
            if hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = datetime.now(UTC)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete_record(self, fighter_id: UUID) -> bool:
        row = self.get_record(fighter_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    # --- Allergies ---

    def add_allergy(self, fighter_id: UUID, data: AllergyCreate) -> Allergy:
        row = Allergy(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_allergies(self, fighter_id: UUID) -> list[Allergy]:
        stmt = (
            select(Allergy)
            .where(Allergy.fighter_id == fighter_id)
            .order_by(Allergy.severity, Allergy.substance)
        )
        return list(self._session.exec(stmt).all())

    def delete_allergy(self, fighter_id: UUID, allergy_id: int) -> bool:
        row = self._session.get(Allergy, allergy_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    # --- Medications ---

    def add_medication(self, fighter_id: UUID, data: MedicationCreate) -> Medication:
        row = Medication(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_medications(self, fighter_id: UUID) -> list[Medication]:
        stmt = (
            select(Medication)
            .where(Medication.fighter_id == fighter_id)
            .order_by(Medication.is_active.desc(), Medication.name)  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt).all())

    def delete_medication(self, fighter_id: UUID, medication_id: int) -> bool:
        row = self._session.get(Medication, medication_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    # --- Conditions ---

    def add_condition(self, fighter_id: UUID, data: MedicalConditionCreate) -> MedicalCondition:
        row = MedicalCondition(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_conditions(self, fighter_id: UUID) -> list[MedicalCondition]:
        stmt = (
            select(MedicalCondition)
            .where(MedicalCondition.fighter_id == fighter_id)
            .order_by(MedicalCondition.status, MedicalCondition.name)
        )
        return list(self._session.exec(stmt).all())

    def delete_condition(self, fighter_id: UUID, condition_id: int) -> bool:
        row = self._session.get(MedicalCondition, condition_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


# ----------------------------------------------------------------------
# Fighter team — titles, sponsors, coach assignments
# ----------------------------------------------------------------------


class FighterTeamRepo:
    """Sub-collections that hang off a fighter for the Team tab.

    Kept separate from FighterRepo so the surface stays small.
    """

    def __init__(self, session: DBSession) -> None:
        self._session = session

    # --- Titles ---

    def add_title(self, fighter_id: UUID, data: FighterTitleCreate) -> FighterTitle:
        row = FighterTitle(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_titles(self, fighter_id: UUID) -> list[FighterTitle]:
        stmt = select(FighterTitle).where(FighterTitle.fighter_id == fighter_id)
        rows = list(self._session.exec(stmt).all())
        rows.sort(key=lambda t: -(t.won_on.toordinal() if t.won_on else 0))
        return rows

    def delete_title(self, fighter_id: UUID, title_id: int) -> bool:
        row = self._session.get(FighterTitle, title_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    # --- Sponsors ---

    def add_sponsor(self, fighter_id: UUID, data: FighterSponsorCreate) -> FighterSponsor:
        row = FighterSponsor(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_sponsors(self, fighter_id: UUID) -> list[FighterSponsor]:
        rows = list(
            self._session.exec(
                select(FighterSponsor).where(FighterSponsor.fighter_id == fighter_id)
            ).all()
        )
        # Sort in Python: current (no end date) first, then most-recent start.
        rows.sort(
            key=lambda s: (
                s.ended_on is not None,
                -(s.started_on.toordinal() if s.started_on else 0),
            )
        )
        return rows

    def delete_sponsor(self, fighter_id: UUID, sponsor_id: int) -> bool:
        row = self._session.get(FighterSponsor, sponsor_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    # --- Coach assignments (denormalises coach name+photo for the UI) ---

    def add_coach_assignment(
        self, fighter_id: UUID, data: CoachAssignmentCreate
    ) -> CoachAssignment:
        row = CoachAssignment(fighter_id=fighter_id, **data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_coach_assignments(
        self, fighter_id: UUID
    ) -> list[tuple[CoachAssignment, str, str | None]]:
        """Returns (assignment, coach_name, coach_photo_path) tuples for the
        UI; the read DTO denormalises the coach so we don't make N+1 calls."""
        stmt = (
            select(CoachAssignment, Coach.name, Coach.photo_path)
            .join(Coach, Coach.id == CoachAssignment.coach_id)  # type: ignore[arg-type]
            .where(CoachAssignment.fighter_id == fighter_id)
        )
        rows = list(self._session.exec(stmt).all())
        rows.sort(
            key=lambda r: (
                r[0].ended_on is not None,
                -(r[0].started_on.toordinal() if r[0].started_on else 0),
            )
        )
        return rows

    def delete_coach_assignment(self, fighter_id: UUID, assignment_id: int) -> bool:
        row = self._session.get(CoachAssignment, assignment_id)
        if row is None or row.fighter_id != fighter_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


class CoachNoteRepo:
    """CRUD for coach notes on fighters."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, coach_id: UUID, fighter_id: UUID, content: str) -> CoachNote:

        row = CoachNote(coach_id=coach_id, fighter_id=fighter_id, content=content)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_for_fighter(self, fighter_id: UUID) -> list[tuple[CoachNote, str, str | None]]:
        """Returns (note, coach_name, coach_photo_path) tuples."""

        stmt = (
            select(CoachNote, Coach.name, Coach.photo_path)
            .join(Coach, Coach.id == CoachNote.coach_id)  # type: ignore[arg-type]
            .where(CoachNote.fighter_id == fighter_id)
            .order_by(CoachNote.created_at.desc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt).all())

    def list_for_coach(self, coach_id: UUID) -> list[tuple[CoachNote, str]]:
        """Returns (note, fighter_name) tuples."""

        stmt = (
            select(CoachNote, Fighter.name)
            .join(Fighter, Fighter.id == CoachNote.fighter_id)  # type: ignore[arg-type]
            .where(CoachNote.coach_id == coach_id)
            .order_by(CoachNote.created_at.desc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt).all())

    def delete(self, note_id: int, coach_id: UUID) -> bool:

        row = self._session.get(CoachNote, note_id)
        if row is None or row.coach_id != coach_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


class IMUSampleRepo:
    """Per-session IMU samples — accelerometer + gyroscope rows.

    Behaves like PunchEventRepo: bulk insert + ordered list. Used by the
    /sessions/{id}/imu/upload endpoint and by the synthetic generator.
    """

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def add_many(self, samples: list[IMUSampleRow]) -> int:
        for s in samples:
            self._session.add(s)
        self._session.commit()
        return len(samples)

    def replace_for_session(self, session_id: UUID, samples: list[IMUSampleRow]) -> int:
        from sqlmodel import delete as sqlmodel_delete

        self._session.exec(
            sqlmodel_delete(IMUSampleRow).where(IMUSampleRow.session_id == session_id)  # type: ignore[arg-type]
        )
        return self.add_many(samples)

    def list_for_session(self, session_id: UUID) -> list[IMUSampleRow]:
        stmt = (
            select(IMUSampleRow)
            .where(IMUSampleRow.session_id == session_id)
            .order_by(IMUSampleRow.t_ms)  # type: ignore[arg-type]
        )
        return list(self._session.exec(stmt).all())

    def count_for_session(self, session_id: UUID) -> int:
        stmt = select(IMUSampleRow).where(IMUSampleRow.session_id == session_id)
        return len(list(self._session.exec(stmt).all()))


class ConsensusEventRepo:
    """Per-session consensus events — one row per reconciled punch."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def replace_for_session(self, session_id: UUID, rows: list[ConsensusEventRow]) -> int:
        from sqlmodel import delete as sqlmodel_delete

        self._session.exec(
            sqlmodel_delete(ConsensusEventRow).where(ConsensusEventRow.session_id == session_id)  # type: ignore[arg-type]
        )
        for r in rows:
            self._session.add(r)
        self._session.commit()
        return len(rows)

    def list_for_session(self, session_id: UUID) -> list[ConsensusEventRow]:
        stmt = (
            select(ConsensusEventRow)
            .where(ConsensusEventRow.session_id == session_id)
            .order_by(ConsensusEventRow.t_ms)  # type: ignore[arg-type]
        )
        return list(self._session.exec(stmt).all())

    def count_for_session(self, session_id: UUID) -> int:
        return len(self.list_for_session(session_id))


class GymManagerRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: GymManagerCreate) -> GymManager:
        row = GymManager(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, manager_id: UUID) -> GymManager | None:
        return self._session.get(GymManager, manager_id)

    def list_all(self) -> list[GymManager]:
        return list(self._session.exec(select(GymManager)).all())

    def list_for_gym(self, gym_id: UUID) -> list[GymManager]:
        stmt = select(GymManager).where(GymManager.gym_id == gym_id)
        return list(self._session.exec(stmt).all())

    def update(self, manager_id: UUID, fields: dict) -> GymManager | None:  # type: ignore[type-arg]
        row = self.get(manager_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, manager_id: UUID) -> bool:
        row = self.get(manager_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


class UserRepo:
    def __init__(self, session: DBSession) -> None:
        self._session = session

    def create(self, data: UserCreate, password_hash: str) -> User:
        user = User(
            email=data.email,
            password_hash=password_hash,
            name=data.name,
            role=data.role,
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return user

    def get(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self._session.exec(stmt).first()

    def set_profile_id(self, user_id: UUID, profile_id: UUID) -> None:
        row = self.get(user_id)
        if row:
            row.profile_id = profile_id
            self._session.add(row)
            self._session.commit()

    def list_all(self) -> list[User]:
        return list(self._session.exec(select(User)).all())

    def update(self, user_id: UUID, fields: dict) -> User | None:  # type: ignore[type-arg]
        row = self.get(user_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def delete(self, user_id: UUID) -> bool:
        row = self.get(user_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True


# ----------------------------------------------------------------------
# Check-in / attendance
# ----------------------------------------------------------------------


class CheckInRepo:
    """Daily gym attendance tracking."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    def check_in(
        self,
        gym_id: UUID,
        member_id: UUID,
        member_type: str,
        notes: str | None = None,
    ) -> CheckIn:
        row = CheckIn(
            gym_id=gym_id,
            member_id=member_id,
            member_type=member_type,
            notes=notes,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def check_out(self, checkin_id: int) -> CheckIn | None:
        row = self._session.get(CheckIn, checkin_id)
        if row is None:
            return None
        row.checked_out_at = datetime.now(UTC)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def list_today(self, gym_id: UUID) -> list[tuple[CheckIn, str]]:
        """Return today's check-ins with member name."""
        from datetime import date as _date

        today_start = datetime(
            _date.today().year, _date.today().month, _date.today().day, tzinfo=UTC
        )
        # Fighters
        f_stmt = (
            select(CheckIn, Fighter.name)
            .join(Fighter, Fighter.id == CheckIn.member_id)  # type: ignore[arg-type]
            .where(
                CheckIn.gym_id == gym_id,
                CheckIn.member_type == "fighter",
                CheckIn.checked_in_at >= today_start,
            )
        )
        # Coaches
        c_stmt = (
            select(CheckIn, Coach.name)
            .join(Coach, Coach.id == CheckIn.member_id)  # type: ignore[arg-type]
            .where(
                CheckIn.gym_id == gym_id,
                CheckIn.member_type == "coach",
                CheckIn.checked_in_at >= today_start,
            )
        )
        rows = list(self._session.exec(f_stmt).all()) + list(self._session.exec(c_stmt).all())
        rows.sort(key=lambda r: r[0].checked_in_at, reverse=True)
        return rows

    def list_for_member(
        self,
        member_id: UUID,
        *,
        limit: int = 30,
    ) -> list[CheckIn]:
        stmt = (
            select(CheckIn)
            .where(CheckIn.member_id == member_id)
            .order_by(CheckIn.checked_in_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    def count_for_member_this_month(self, member_id: UUID) -> int:
        from datetime import date as _date

        first_of_month = datetime(_date.today().year, _date.today().month, 1, tzinfo=UTC)
        stmt = select(CheckIn).where(
            CheckIn.member_id == member_id,
            CheckIn.checked_in_at >= first_of_month,
        )
        return len(list(self._session.exec(stmt).all()))


class DatasetRepo:
    """Datasets (ADR-013): datasets, their participants' consent, and takes. Consent
    is checked here — the one place a take is created — so nothing can record a
    fighter without `self` or `irb_signed` consent."""

    def __init__(self, session: DBSession) -> None:
        self._session = session

    # -- datasets --
    def create(self, data: DatasetCreate) -> Dataset:
        row = Dataset(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, dataset_id: UUID) -> Dataset | None:
        return self._session.get(Dataset, dataset_id)

    def list_all(self) -> list[Dataset]:
        stmt = select(Dataset).order_by(Dataset.created_at.desc())  # type: ignore[attr-defined]
        return list(self._session.exec(stmt).all())

    # -- participants --
    def participants(self, dataset_id: UUID) -> list[DatasetParticipant]:
        stmt = select(DatasetParticipant).where(DatasetParticipant.dataset_id == dataset_id)
        return list(self._session.exec(stmt).all())

    def participant(self, dataset_id: UUID, fighter_id: UUID) -> DatasetParticipant | None:
        stmt = select(DatasetParticipant).where(
            DatasetParticipant.dataset_id == dataset_id,
            DatasetParticipant.fighter_id == fighter_id,
        )
        return self._session.exec(stmt).first()

    def set_participant(self, dataset_id: UUID, data: DatasetParticipantIn) -> DatasetParticipant:
        """Add a fighter to the dataset or update their consent. Raises ValueError if a
        second participant would be `self` (only the researcher records himself)."""
        if self._session.get(Fighter, data.fighter_id) is None:
            raise ValueError("fighter not found")
        if data.consent == ConsentEnum.SELF:
            others = [
                p
                for p in self.participants(dataset_id)
                if p.consent == ConsentEnum.SELF and p.fighter_id != data.fighter_id
            ]
            if others:
                raise ValueError("another participant is already the self-recording researcher")
        row = self.participant(dataset_id, data.fighter_id)
        if row is None:
            row = DatasetParticipant(
                dataset_id=dataset_id, code=self._next_code(dataset_id), **data.model_dump()
            )
        else:
            for k, v in data.model_dump().items():
                setattr(row, k, v)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def _next_code(self, dataset_id: UUID) -> str:
        taken = [
            int(p.code[len(PARTICIPANT_CODE_PREFIX) :])
            for p in self.participants(dataset_id)
            if p.code and p.code[len(PARTICIPANT_CODE_PREFIX) :].isdigit()
        ]
        return participant_code(max(taken, default=0) + 1)

    def ensure_codes(self, dataset_id: UUID) -> list[DatasetParticipant]:
        """The dataset's participants, giving a code to any that lack one (the
        migration numbers existing ones; this covers a database made without it)."""
        rows = sorted(self.participants(dataset_id), key=lambda p: p.created_at)
        missing = [p for p in rows if not p.code]
        for p in missing:
            p.code = self._next_code(dataset_id)
            self._session.add(p)
            self._session.commit()
        return rows

    def participant_by_code(self, dataset_id: UUID, code: str) -> DatasetParticipant | None:
        stmt = select(DatasetParticipant).where(
            DatasetParticipant.dataset_id == dataset_id,
            DatasetParticipant.code == code.strip().upper(),
        )
        return self._session.exec(stmt).first()

    def withdraw_participant(self, participant: DatasetParticipant) -> None:
        """Mark them withdrawn (their takes are deleted by the caller): they can't be
        recorded again, and their code stays taken so it's never reused."""
        participant.consent = ConsentEnum.WITHDRAWN
        self._session.add(participant)
        self._session.commit()

    # -- takes --
    def create_take(self, dataset_id: UUID, fighter_id: UUID) -> DatasetTake:
        """Raises ValueError unless the fighter is a participant whose consent allows
        recording."""
        p = self.participant(dataset_id, fighter_id)
        if p is None:
            raise ValueError("fighter is not a participant of this dataset")
        if not ConsentEnum(p.consent).may_record:
            raise ValueError(f"consent is '{p.consent}' — record only with self or IRB consent")
        row = DatasetTake(dataset_id=dataset_id, fighter_id=fighter_id)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get_take(self, take_id: UUID) -> DatasetTake | None:
        return self._session.get(DatasetTake, take_id)

    def takes(self, dataset_id: UUID) -> list[DatasetTake]:
        stmt = (
            select(DatasetTake)
            .where(DatasetTake.dataset_id == dataset_id)
            .order_by(DatasetTake.started_at.desc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(stmt).all())

    def delete_take(self, take_id: UUID) -> bool:
        row = self.get_take(take_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def finish_take(
        self, take_id: UUID, status: TakeStatusEnum, duration_ms: float | None = None
    ) -> DatasetTake | None:
        row = self.get_take(take_id)
        if row is None:
            return None
        row.status = status
        if status == TakeStatusEnum.COMPLETED:
            row.ended_at = datetime.now(UTC)
            if duration_ms is not None:
                row.duration_ms = duration_ms
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row
