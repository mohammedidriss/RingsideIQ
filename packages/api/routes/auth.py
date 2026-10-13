"""Authentication routes — register, login, me."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator
from sqlmodel import Session as DBSession
from sqlmodel import select

from api.deps import db_session
from api.services import terms
from store import (
    CoachRepo,
    FighterRepo,
    GymManagerRepo,
    GymRepo,
    RefereeRepo,
    User,
    UserCreate,
    UserRead,
    UserRepo,
    UserRole,
)
from store.models import CoachCreate, FighterCreate, GymManagerCreate, RefereeCreate

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Security config ---
_DEV_SECRET = "alion-dev-secret-key-change-in-production"


def _is_production() -> bool:
    """True when running on a real deployment.

    Triggered by an explicit ALION_ENV=production/prod, or by Railway's
    auto-injected RAILWAY_ENVIRONMENT var so we fail closed on the host
    even if ALION_ENV was never set.
    """
    if os.environ.get("ALION_ENV", "").strip().lower() in {"production", "prod"}:
        return True
    return bool(os.environ.get("RAILWAY_ENVIRONMENT", "").strip())


def _resolve_secret_key() -> str:
    """Return the JWT signing key, or fail closed in production.

    In production a missing/default secret is fatal: a known key lets anyone
    forge admin tokens. In dev we warn and fall back to the built-in default.
    """
    secret = os.environ.get("ALION_JWT_SECRET", "").strip() or _DEV_SECRET
    if secret == _DEV_SECRET:
        if _is_production():
            raise RuntimeError(
                "ALION_JWT_SECRET must be set in production. Refusing to start with "
                "the insecure built-in default. Generate one with "
                "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` and "
                "set it as the ALION_JWT_SECRET environment variable."
            )
        import warnings

        warnings.warn(
            "ALION_JWT_SECRET is not set — using insecure default. "
            "Set this env var before deploying to production.",
            stacklevel=2,
        )
    return secret


SECRET_KEY = _resolve_secret_key()
ALGORITHM = "HS256"
# Token lifetime. Override with ALION_TOKEN_TTL_MIN. Defaults to 12h in
# production (shorter blast radius if a token leaks) and 7 days in dev.
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("ALION_TOKEN_TTL_MIN", "") or (60 * 12 if _is_production() else 60 * 24 * 7)
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Helpers ---


def _hash_password(password: str) -> str:
    return str(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())


def _verify_password(plain: str, hashed: str) -> bool:
    return bool(bcrypt.checkpw(plain.encode(), hashed.encode()))


_MIN_PASSWORD_LEN = 8


def _validate_password_strength(password: str) -> str:
    """Enforce a minimum password policy. Returns the password unchanged or raises.

    Kept deliberately simple: a length floor plus a mixed-content check.
    Raises ValueError so Pydantic validators surface it as a 422.
    """
    if len(password) < _MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {_MIN_PASSWORD_LEN} characters")
    if password.isalpha() or password.isdigit():
        raise ValueError("Password must contain both letters and numbers")
    return password


def _create_access_token(user_id: UUID, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return str(jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM))


def _user_repo(session: DBSession = Depends(db_session)) -> UserRepo:
    return UserRepo(session)


# --- Dependency: get current user from token ---


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: DBSession = Depends(db_session),
) -> User | None:
    """Returns the current user or None if not authenticated."""
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    repo = UserRepo(session)
    user = repo.get(UUID(user_id))
    if user is None or not user.is_active:
        return None
    return user


async def require_signed_in(
    user: User | None = Depends(get_current_user),
) -> User:
    """Like get_current_user but raises 401 if not authenticated. Doesn't check the
    terms — only for the routes that let someone see and accept them."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


TERMS_NOT_ACCEPTED = "terms_not_accepted"


async def require_current_user(user: User = Depends(require_signed_in)) -> User:
    """A signed-in user who has accepted the current terms (ADR-016) — 403 until
    they do, so nothing is used before they've agreed."""
    if user.terms_version != terms.TERMS_VERSION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{TERMS_NOT_ACCEPTED}: accept the terms and conditions "
            f"(version {terms.TERMS_VERSION}) to continue",
        )
    return user


# --- Request / Response schemas ---


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    role: UserRole = UserRole.FIGHTER
    # Signing up means agreeing to the terms shown (ADR-016): both are required.
    accept_terms: bool = False
    terms_version: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("Name is required")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


# --- Routes ---


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    data: RegisterRequest,
    repo: UserRepo = Depends(_user_repo),
    session: DBSession = Depends(db_session),
) -> TokenResponse:
    """Create a new user account, auto-create a linked profile, and return a JWT.
    Refused unless the current terms were accepted."""
    if not data.accept_terms or data.terms_version != terms.TERMS_VERSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Accept the terms and conditions to create an account"
            + (
                f" (they've changed — the current version is {terms.TERMS_VERSION})"
                if data.accept_terms
                else ""
            ),
        )
    existing = repo.get_by_email(data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user_create = UserCreate(
        email=data.email,
        password=data.password,
        name=data.name,
        role=data.role,
    )
    hashed = _hash_password(data.password)
    user = repo.create(user_create, hashed)
    user = repo.accept_terms(user, terms.TERMS_VERSION)

    # Auto-create a linked profile based on role
    profile_id = _create_profile_for_role(user, session)
    if profile_id:
        repo.set_profile_id(user.id, profile_id)
        user.profile_id = profile_id

    token = _create_access_token(user.id, user.role)
    return TokenResponse(
        access_token=token,
        user=UserRead.model_validate(user, from_attributes=True),
    )


def _create_profile_for_role(
    user: User,
    session: DBSession,
    gym_id: UUID | None = None,
) -> UUID | None:
    """Create a profile entity matching the user's role and return its id."""
    if user.role == UserRole.FIGHTER:
        f_repo = FighterRepo(session)
        f = f_repo.create(FighterCreate(name=user.name, stance="orthodox"))
        return f.id
    if user.role == UserRole.COACH:
        c_repo = CoachRepo(session)
        c = c_repo.create(CoachCreate(name=user.name))
        return c.id
    if user.role == UserRole.REFEREE:
        r_repo = RefereeRepo(session)
        r = r_repo.create(RefereeCreate(name=user.name))
        return r.id
    if user.role == UserRole.GYM_MANAGER and gym_id is not None:
        # Validate gym exists
        g_repo = GymRepo(session)
        if g_repo.get(gym_id) is None:
            raise HTTPException(status_code=404, detail="Gym not found")
        gm_repo = GymManagerRepo(session)
        gm = gm_repo.create(GymManagerCreate(name=user.name, gym_id=gym_id))
        return gm.id
    return None


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    repo: UserRepo = Depends(_user_repo),
) -> TokenResponse:
    """Authenticate with email + password, return a JWT."""
    user = repo.get_by_email(form_data.username.lower().strip())
    if user is None or not _verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    token = _create_access_token(user.id, user.role)
    return TokenResponse(
        access_token=token,
        user=UserRead.model_validate(user, from_attributes=True),
    )


@router.get("/me", response_model=UserRead)
def get_me(user: User = Depends(require_signed_in)) -> UserRead:
    """Return the current authenticated user (also before they've accepted the
    terms: `terms_version` tells the dashboard to ask)."""
    return UserRead.model_validate(user, from_attributes=True)


class TermsOut(BaseModel):
    version: str
    text: str  # Markdown
    placeholder: bool  # the draft text, not yet the approved terms


@router.get("/terms", response_model=TermsOut)
def get_terms() -> TermsOut:
    """The current terms and conditions — public, so the sign-up form can show them."""
    return TermsOut(
        version=terms.TERMS_VERSION, text=terms.text(), placeholder=terms.is_placeholder()
    )


class AcceptTermsRequest(BaseModel):
    version: str


@router.post("/terms/accept", response_model=UserRead)
def accept_terms(
    data: AcceptTermsRequest,
    user: User = Depends(require_signed_in),
    repo: UserRepo = Depends(_user_repo),
) -> UserRead:
    """Record that the signed-in user accepted the terms — only the current version
    (someone who read an older one is asked to read the new one)."""
    if data.version != terms.TERMS_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The terms have changed — the current version is {terms.TERMS_VERSION}",
        )
    return UserRead.model_validate(repo.accept_terms(user, data.version), from_attributes=True)


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    current_password: str | None = None
    new_password: str | None = None


@router.patch("/me", response_model=UserRead)
def update_me(
    data: UpdateProfileRequest,
    user: User = Depends(require_current_user),
    repo: UserRepo = Depends(_user_repo),
    session: DBSession = Depends(db_session),
) -> UserRead:
    """Update the current user's profile (name, email, password)."""
    fields: dict[str, Any] = {}

    if data.name is not None:
        name = data.name.strip()
        if len(name) < 1:
            raise HTTPException(status_code=422, detail="Name cannot be empty")
        fields["name"] = name
        # Also sync name on linked profile
        if user.profile_id:
            _sync_profile_name(user, name, session)

    if data.email is not None:
        email = data.email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise HTTPException(status_code=422, detail="Invalid email format")
        if email != user.email:
            existing = repo.get_by_email(email)
            if existing:
                raise HTTPException(status_code=409, detail="Email already in use")
            fields["email"] = email

    if data.new_password is not None:
        if not data.current_password:
            raise HTTPException(
                status_code=422, detail="Current password is required to set a new one"
            )
        if not _verify_password(data.current_password, user.password_hash):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        try:
            _validate_password_strength(data.new_password)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        fields["password_hash"] = _hash_password(data.new_password)

    if not fields:
        return UserRead.model_validate(user, from_attributes=True)

    updated = repo.update(user.id, fields)
    return UserRead.model_validate(updated, from_attributes=True)


def _sync_profile_name(user: User, new_name: str, session: DBSession) -> None:
    """Keep the linked profile's name in sync with the user account name."""
    role = str(user.role).lower()
    if role == "fighter":
        f_repo = FighterRepo(session)
        f = f_repo.get(user.profile_id)  # type: ignore[arg-type]
        if f:
            f.name = new_name
            session.add(f)
            session.commit()
    elif role == "coach":
        c_repo = CoachRepo(session)
        c = c_repo.get(user.profile_id)  # type: ignore[arg-type]
        if c:
            c.name = new_name
            session.add(c)
            session.commit()
    elif role == "gym_manager":
        gm_repo = GymManagerRepo(session)
        gm = gm_repo.get(user.profile_id)  # type: ignore[arg-type]
        if gm:
            gm.name = new_name
            session.add(gm)
            session.commit()


# --- Admin-only helpers ---


def _require_admin(user: User = Depends(require_current_user)) -> User:
    """Dependency that enforces admin role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# --- Admin endpoints ---


@router.get("/admin/users", response_model=list[UserRead])
def admin_list_users(
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> list[UserRead]:
    """List all users in the system (admin only)."""
    return [UserRead.model_validate(u, from_attributes=True) for u in repo.list_all()]


class UnifiedPerson(BaseModel):
    """A merged view of every person in the system — login account or profile-only."""

    id: UUID  # profile id (or user id for admin/unlinked accounts)
    user_id: UUID | None = None  # set when a login account exists
    name: str
    email: str | None = None
    role: str
    has_account: bool
    is_active: bool
    photo_path: str | None = None


@router.get("/admin/everyone", response_model=list[UnifiedPerson])
def admin_list_everyone(
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
    fighters: FighterRepo = Depends(lambda s=Depends(db_session): FighterRepo(s)),
    coaches: CoachRepo = Depends(lambda s=Depends(db_session): CoachRepo(s)),
    referees: RefereeRepo = Depends(lambda s=Depends(db_session): RefereeRepo(s)),
    gym_managers: GymManagerRepo = Depends(lambda s=Depends(db_session): GymManagerRepo(s)),
) -> list[UnifiedPerson]:
    """Unified view: every profile (fighter/coach/etc.) + every login account.

    Profiles that have a linked user account are shown once with has_account=True.
    Profiles with no login are shown with has_account=False.
    Login accounts with no profile (e.g. bare admin accounts) are shown too.
    """
    # Build a map from profile_id → user for fast lookup
    all_users = repo.list_all()
    profile_to_user = {u.profile_id: u for u in all_users if u.profile_id}
    accounted_user_ids = set()

    result: list[UnifiedPerson] = []

    def _add_profiles(profiles: list[Any], role: str) -> None:
        for p in profiles:
            user = profile_to_user.get(p.id)
            if user:
                accounted_user_ids.add(user.id)
            result.append(
                UnifiedPerson(
                    id=p.id,
                    user_id=user.id if user else None,
                    name=p.name,
                    email=user.email if user else getattr(p, "email", None),
                    role=role,
                    has_account=user is not None,
                    is_active=user.is_active if user else True,
                    photo_path=getattr(p, "photo_path", None),
                )
            )

    _add_profiles(fighters.list_all(), "fighter")
    _add_profiles(coaches.list_all(), "coach")
    _add_profiles(referees.list_all(), "referee")
    _add_profiles(gym_managers.list_all(), "gym_manager")

    # Add any user accounts not yet represented (e.g. admins, unlinked accounts)
    for u in all_users:
        if u.id not in accounted_user_ids:
            result.append(
                UnifiedPerson(
                    id=u.id,
                    user_id=u.id,
                    name=u.name,
                    email=u.email,
                    role=u.role,
                    has_account=True,
                    is_active=u.is_active,
                    photo_path=u.photo_path,
                )
            )

    return sorted(result, key=lambda p: (p.role, p.name.lower()))


class AdminCreateUserRequest(BaseModel):
    email: str
    password: str
    name: str
    role: UserRole
    gym_id: UUID | None = None  # required when role == gym_manager

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)


@router.post("/admin/users", response_model=UserRead, status_code=201)
def admin_create_user(
    data: AdminCreateUserRequest,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
    session: DBSession = Depends(db_session),
) -> UserRead:
    """Create a user account (admin only). Auto-creates a linked profile for all roles."""
    if data.role == UserRole.GYM_MANAGER and data.gym_id is None:
        raise HTTPException(status_code=422, detail="gym_id is required when role is gym_manager")
    existing = repo.get_by_email(data.email.lower().strip())
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user_create = UserCreate(
        email=data.email.lower().strip(),
        password=data.password,
        name=data.name.strip(),
        role=data.role,
    )
    hashed = _hash_password(data.password)
    user = repo.create(user_create, hashed)
    profile_id = _create_profile_for_role(user, session, gym_id=data.gym_id)
    if profile_id:
        repo.set_profile_id(user.id, profile_id)
        user.profile_id = profile_id
    return UserRead.model_validate(user, from_attributes=True)


class AdminResetPasswordRequest(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: UUID,
    data: AdminResetPasswordRequest,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> dict[str, str]:
    """Reset any user's password (admin only)."""
    target = repo.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    new_hash = _hash_password(data.new_password)
    repo.update(user_id, {"password_hash": new_hash})
    return {"status": "ok", "message": f"Password reset for {target.email}"}


class AdminUpdateUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    profile_id: UUID | None = None


@router.patch("/admin/users/{user_id}", response_model=UserRead)
def admin_update_user(
    user_id: UUID,
    data: AdminUpdateUserRequest,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> UserRead:
    """Update any user's fields (admin only)."""
    target = repo.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = repo.update(user_id, fields)
    return UserRead.model_validate(updated, from_attributes=True)


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: UUID,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> dict[str, str]:
    """Delete a user account (admin only). Cannot delete yourself."""
    if user_id == _admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
    target = repo.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.delete(user_id)
    return {"status": "ok", "message": f"Deleted user {target.email}"}


@router.post("/admin/users/{user_id}/deactivate")
def admin_deactivate_user(
    user_id: UUID,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> dict[str, str]:
    """Deactivate a user (soft disable, preserves data)."""
    if user_id == _admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    target = repo.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.update(user_id, {"is_active": False})
    return {"status": "ok", "message": f"Deactivated {target.email}"}


@router.post("/admin/users/{user_id}/activate")
def admin_activate_user(
    user_id: UUID,
    _admin: User = Depends(_require_admin),
    repo: UserRepo = Depends(_user_repo),
) -> dict[str, str]:
    """Re-activate a previously deactivated user."""
    target = repo.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.update(user_id, {"is_active": True})
    return {"status": "ok", "message": f"Activated {target.email}"}


class SeedAdminRequest(BaseModel):
    email: str
    password: str
    name: str
    seed_secret: str


@router.post("/seed-admin", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def seed_admin(
    data: SeedAdminRequest,
    repo: UserRepo = Depends(_user_repo),
    session: DBSession = Depends(db_session),
) -> UserRead:
    """Create the first admin account.

    Protected by ALION_SEED_SECRET env var — set it on Railway, call this
    endpoint once, then remove the env var to disable it permanently.
    Returns 403 if the env var is not set or the secret doesn't match.
    Returns 409 if an admin already exists.
    """
    expected = os.environ.get("ALION_SEED_SECRET", "")
    if not expected or data.seed_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing seed secret")
    # Prevent creating a second admin via this endpoint
    all_users = list(session.exec(select(User)).all())
    if any(u.role in (UserRole.ADMIN, "admin") for u in all_users):
        raise HTTPException(status_code=409, detail="An admin account already exists")
    existing = repo.get_by_email(data.email.lower().strip())
    if existing:
        # Promote existing account to admin instead of creating a duplicate
        updated = repo.update(existing.id, {"role": UserRole.ADMIN, "is_active": True})
        return UserRead.model_validate(updated, from_attributes=True)
    user = repo.create(
        UserCreate(
            email=data.email.lower().strip(),
            password=data.password,
            name=data.name.strip(),
            role=UserRole.ADMIN,
        ),
        _hash_password(data.password),
    )
    return UserRead.model_validate(user, from_attributes=True)


class AdminSystemStats(BaseModel):
    total_users: int
    active_users: int
    fighters: int
    coaches: int
    gym_managers: int
    admins: int
    gyms: int
    sessions: int


@router.get("/admin/stats", response_model=AdminSystemStats)
def admin_system_stats(
    _admin: User = Depends(_require_admin),
    session: DBSession = Depends(db_session),
) -> AdminSystemStats:
    """System-wide statistics (admin only)."""
    from store.models import Gym
    from store.models import Session as TrainingSession

    users = list(session.exec(select(User)).all())
    return AdminSystemStats(
        total_users=len(users),
        active_users=sum(1 for u in users if u.is_active),
        fighters=sum(1 for u in users if u.role in (UserRole.FIGHTER, "fighter")),
        coaches=sum(1 for u in users if u.role in (UserRole.COACH, "coach")),
        gym_managers=sum(1 for u in users if u.role in (UserRole.GYM_MANAGER, "gym_manager")),
        admins=sum(1 for u in users if u.role in (UserRole.ADMIN, "admin")),
        gyms=len(list(session.exec(select(Gym)).all())),
        sessions=len(list(session.exec(select(TrainingSession)).all())),
    )
