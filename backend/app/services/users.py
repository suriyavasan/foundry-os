from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFound
from app.core.principal import Principal, PrincipalType
from app.models.company import Company, CompanyMember
from app.models.user import User

LOCAL_OWNER_EMAIL = "owner@localhost"
LOCAL_OWNER_NAME = "Owner"


def ensure_local_owner(factory: sessionmaker[Session]) -> str:
    """Create the single local owner user if missing. Returns its id."""
    with factory() as db:
        user = db.scalar(select(User).where(User.email == LOCAL_OWNER_EMAIL))
        if user is None:
            user = User(email=LOCAL_OWNER_EMAIL, display_name=LOCAL_OWNER_NAME)
            db.add(user)
            db.commit()
        return user.id


def principal_for_user(user: User) -> Principal:
    return Principal(type=PrincipalType.HUMAN, id=user.id, label=user.display_name)


def get_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


def get_local_owner(db: Session) -> User | None:
    return db.scalar(select(User).where(User.email == LOCAL_OWNER_EMAIL))


def list_memberships(db: Session, user_id: str) -> list[tuple[CompanyMember, Company]]:
    stmt = (
        select(CompanyMember, Company)
        .join(Company, Company.id == CompanyMember.company_id)
        .where(CompanyMember.user_id == user_id)
        .order_by(Company.created_at, Company.id)
    )
    return [(m, c) for m, c in db.execute(stmt).all()]
