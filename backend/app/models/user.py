from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # M7: an email is optional (setup asks for it only for invites/recovery). Unique when present.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # M7: login name (3-32 of [a-z0-9._-]); NULL until the account is claimed / created with one.
    username: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # Argon2id encoded hash (carries salt + parameters). NULL = no credentials yet.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The placeholder owner created at startup (M3b-sec): what the owner token and setup claim.
    is_local_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # TOTP 2FA: the secret is encrypted at rest (Fernet); recovery codes are stored hashed.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
