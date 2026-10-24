from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFound
from app.core.principal import Principal, PrincipalType
from app.models.company import Company, CompanyMember
from app.models.enums import MemberStatus
from app.models.user import User

LOCAL_OWNER_EMAIL = "owner@localhost"
LOCAL_OWNER_NAME = "Owner"


def ensure_local_owner(factory: sessionmaker[Session]) -> str:
    """Create the local owner user if missing. Returns its id.

    That user is what the owner token (CLI / bootstrap) acts as, and what the first-run setup
    claims. After a claim it has a username and password and a different email, so it is found by
    the ``is_local_owner`` flag, never by its placeholder email."""
    with factory() as db:
        user = db.scalar(
            select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
        )
        if user is None:
            user = db.scalar(select(User).where(User.email == LOCAL_OWNER_EMAIL))
            if user is not None:  # a pre-0007 row the migration did not flag
                user.is_local_owner = True
            else:
                user = User(
                    email=LOCAL_OWNER_EMAIL, display_name=LOCAL_OWNER_NAME, is_local_owner=True
                )
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
    return db.scalar(
        select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
    )


def list_memberships(db: Session, user_id: str) -> list[tuple[CompanyMember, Company]]:
    stmt = (
        select(CompanyMember, Company)
        .join(Company, Company.id == CompanyMember.company_id)
        .where(CompanyMember.user_id == user_id, CompanyMember.status == MemberStatus.ACTIVE)
        .order_by(Company.created_at, Company.id)
    )
    return [(m, c) for m, c in db.execute(stmt).all()]
