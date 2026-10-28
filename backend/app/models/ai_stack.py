"""M17: the company's ordered stack of AI connections (docs/specs/m17-ai-stack.md)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class AiConnection(TimestampMixin, Base):
    """One way the company can think: a model API or a coding CLI (plan / key / provider).
    Never holds a secret: only ids of credentials / providers."""

    __tablename__ = "ai_connections"
    __table_args__ = (Index("ix_ai_connections_company_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    engine: Mapped[str] = mapped_column(String(8), nullable=False)  # api | cli
    model_provider_id: Mapped[str | None] = mapped_column(ForeignKey("model_providers.id"))
    model: Mapped[str | None] = mapped_column(String(160))
    cli_id: Mapped[str | None] = mapped_column(String(32))
    auth_mode: Mapped[str | None] = mapped_column(String(16))  # plan | api_key | provider
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"))
    cli_provider_id: Mapped[str | None] = mapped_column(ForeignKey("model_providers.id"))
    use_for: Mapped[str] = mapped_column(
        String(16), default="everything", server_default="everything", nullable=False
    )
    share: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Plan connections: the CLI's private home, relative to the data directory.
    home_dir: Mapped[str | None] = mapped_column(String(255))
    last_test: Mapped[dict | None] = mapped_column(JSON)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pause_reason: Mapped[str | None] = mapped_column(String(200))
