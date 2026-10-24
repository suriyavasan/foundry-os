"""M10 Command Center (docs/specs/m10 section 5)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

OutputKindLiteral = Literal["text", "table", "list", "error"]
InputKindLiteral = Literal["command", "note"]
ExchangeStatusLiteral = Literal["done", "pending", "processing", "answered", "failed"]
MAX_INPUT_CHARS = 10_240


class CommandRequest(RequestModel):
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


class CommandLink(ApiModel):
    label: str
    panel: str  # a canvas panel string: "tasks", "approvals", "agent:<id>", "reflection:<id>"...


class CommandOutput(ApiModel):
    kind: OutputKindLiteral
    title: str
    text: str
    columns: list[str] | None = None
    rows: list[list[str]] | None = None  # table: aligned with columns; list: one item per row
    links: list[CommandLink] | None = None


class CommandAction(ApiModel):
    type: str
    ref_id: str
    label: str | None = None


class CommandExchange(ApiModel):
    id: str
    input: str
    input_kind: InputKindLiteral
    created_at: Timestamp
    status: ExchangeStatusLiteral
    output: CommandOutput | None
    note_id: str | None
    run_id: str | None
    actions: list[CommandAction]
    updated_at: Timestamp
