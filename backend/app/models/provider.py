"""Credentials (encrypted at rest) and model providers (where agents get their LLM)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class Credential(TimestampMixin, Base):
    """A secret (API key, token). ``encrypted_value`` is a Fernet token; the plaintext is
    never returned by any API, logged, audited or put into an event. It is decrypted only by
    the runtime adapter right before the value is written to the runtime's stdin."""

    __tablename__ = "credentials"
    __table_args__ = (
        Index("ix_credentials_company_id", "company_id"),
        # A name is unique among live credentials; a revoked one frees its name.
        Index(
            "uq_credentials_live_name",
            "company_id",
            "name",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ModelProvider(TimestampMixin, Base):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_model_providers_company_id"),
        Index("ix_model_providers_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"))
    default_model: Mapped[str] = mapped_column(String(160), nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=32768, nullable=False)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # null = unknown price: cost is then "unknown", never a made-up number.
    input_price_per_mtok_micros: Mapped[int | None] = mapped_column(BigInteger)
    output_price_per_mtok_micros: Mapped[int | None] = mapped_column(BigInteger)
    # Outcome of the last connectivity test: {status, message, checked_at, ...} (no secrets).
    last_test: Mapped[dict | None] = mapped_column(JSON)
