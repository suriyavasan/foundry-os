"""M18 Part B: LangGraph checkpoint models for durable work graphs.

See docs/specs/m18-low-friction-autonomy.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import UTCDateTime


class GraphCheckpoint(Base):
    """M18: LangGraph checkpoint snapshot."""

    __tablename__ = "graph_checkpoints"
    __table_args__ = (
        Index("ix_graph_checkpoints_company_id_thread_id", "company_id", "thread_id"),
    )

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        String(255), primary_key=True, default="", server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        if "metadata" in kwargs and "metadata_" not in kwargs:
            kwargs["metadata_"] = kwargs.pop("metadata")
        super().__init__(**kwargs)


class GraphWrite(Base):
    """M18: LangGraph pending/intermediate write."""

    __tablename__ = "graph_writes"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        String(255), primary_key=True, default="", server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    task_path: Mapped[str] = mapped_column(
        String(255), default="", server_default="", nullable=False
    )
