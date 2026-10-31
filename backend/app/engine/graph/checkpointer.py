"""M18 Part B: LangGraph SQL checkpointer (docs/specs/m18-low-friction-autonomy.md)."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Self

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
    writes_sort_key,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.graph import GraphCheckpoint, GraphWrite

RETENTION_LIMIT = 20


class SqlCheckpointSaver(BaseCheckpointSaver[str]):
    """Stores LangGraph checkpoints and writes in company-partitioned relational tables."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        serde: SerializerProtocol | None = None,
    ) -> None:
        super().__init__(serde=serde)
        self.session_factory = session_factory

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Fetch a checkpoint tuple using the given configuration."""
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id:
            return None
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        company_id = configurable.get("company_id")

        with self.session_factory() as session:
            if checkpoint_id:
                stmt = select(GraphCheckpoint).where(
                    GraphCheckpoint.thread_id == thread_id,
                    GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    GraphCheckpoint.checkpoint_id == checkpoint_id,
                )
                if company_id is not None:
                    stmt = stmt.where(GraphCheckpoint.company_id == company_id)
                row = session.scalars(stmt).first()
            else:
                stmt = (
                    select(GraphCheckpoint)
                    .where(
                        GraphCheckpoint.thread_id == thread_id,
                        GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    )
                    .order_by(GraphCheckpoint.checkpoint_id.desc())
                )
                if company_id is not None:
                    stmt = stmt.where(GraphCheckpoint.company_id == company_id)
                row = session.scalars(stmt).first()

            if row is None:
                return None

            writes_stmt = select(GraphWrite).where(
                GraphWrite.thread_id == thread_id,
                GraphWrite.checkpoint_ns == checkpoint_ns,
                GraphWrite.checkpoint_id == row.checkpoint_id,
            )
            if company_id is not None:
                writes_stmt = writes_stmt.where(GraphWrite.company_id == company_id)
            write_rows = session.scalars(writes_stmt).all()

            sorted_writes = sorted(
                write_rows,
                key=lambda w: writes_sort_key(w.task_path, w.task_id, w.idx),
            )
            pending_writes: list[PendingWrite] = [
                (w.task_id, w.channel, self.serde.loads_typed((w.type, w.value)))
                for w in sorted_writes
            ]

            checkpoint_dict = self.serde.loads_typed((row.type, row.checkpoint))

            out_config: RunnableConfig = {
                "configurable": {
                    **configurable,
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            }
            parent_config: RunnableConfig | None = (
                {
                    "configurable": {
                        **({"company_id": row.company_id} if company_id else {}),
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            )

            return CheckpointTuple(
                config=out_config,
                checkpoint=checkpoint_dict,
                metadata=dict(row.metadata_),
                parent_config=parent_config,
                pending_writes=pending_writes,
            )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints that match the given criteria, newest first."""
        if limit is not None and limit <= 0:
            return

        configurable = config.get("configurable", {}) if config else {}
        company_id = configurable.get("company_id")
        thread_id = configurable.get("thread_id")
        checkpoint_ns = configurable.get("checkpoint_ns")
        checkpoint_id = get_checkpoint_id(config) if config else None
        before_id = get_checkpoint_id(before) if before else None

        stmt = select(GraphCheckpoint)
        if company_id is not None:
            stmt = stmt.where(GraphCheckpoint.company_id == company_id)
        if thread_id is not None:
            stmt = stmt.where(GraphCheckpoint.thread_id == thread_id)
        if checkpoint_ns is not None:
            stmt = stmt.where(GraphCheckpoint.checkpoint_ns == checkpoint_ns)
        if checkpoint_id is not None:
            stmt = stmt.where(GraphCheckpoint.checkpoint_id == checkpoint_id)
        if before_id is not None:
            stmt = stmt.where(GraphCheckpoint.checkpoint_id < before_id)

        stmt = stmt.order_by(GraphCheckpoint.checkpoint_id.desc())

        tuples: list[CheckpointTuple] = []
        with self.session_factory() as session:
            rows = session.scalars(stmt).all()
            for row in rows:
                meta = dict(row.metadata_)
                if filter and not all(meta.get(k) == v for k, v in filter.items()):
                    continue

                writes_stmt = select(GraphWrite).where(
                    GraphWrite.thread_id == row.thread_id,
                    GraphWrite.checkpoint_ns == row.checkpoint_ns,
                    GraphWrite.checkpoint_id == row.checkpoint_id,
                )
                if company_id is not None:
                    writes_stmt = writes_stmt.where(GraphWrite.company_id == company_id)
                write_rows = session.scalars(writes_stmt).all()

                sorted_writes = sorted(
                    write_rows,
                    key=lambda w: writes_sort_key(w.task_path, w.task_id, w.idx),
                )
                pending_writes: list[PendingWrite] = [
                    (w.task_id, w.channel, self.serde.loads_typed((w.type, w.value)))
                    for w in sorted_writes
                ]

                checkpoint_dict = self.serde.loads_typed((row.type, row.checkpoint))

                out_config: RunnableConfig = {
                    "configurable": {
                        **configurable,
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.checkpoint_id,
                    }
                }
                parent_config: RunnableConfig | None = (
                    {
                        "configurable": {
                            **({"company_id": row.company_id} if company_id else {}),
                            "thread_id": row.thread_id,
                            "checkpoint_ns": row.checkpoint_ns,
                            "checkpoint_id": row.parent_checkpoint_id,
                        }
                    }
                    if row.parent_checkpoint_id
                    else None
                )

                tuples.append(
                    CheckpointTuple(
                        config=out_config,
                        checkpoint=checkpoint_dict,
                        metadata=meta,
                        parent_config=parent_config,
                        pending_writes=pending_writes,
                    )
                )
                if limit is not None and len(tuples) >= limit:
                    break

        yield from tuples

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store a checkpoint and prune old checkpoints to enforce retention."""
        configurable = config.get("configurable", {})
        company_id = configurable.get("company_id")
        if not company_id:
            raise ValueError("company_id is required in config['configurable'] for put")

        thread_id = configurable["thread_id"]
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = configurable.get("checkpoint_id")

        type_, blob = self.serde.dumps_typed(checkpoint)
        meta = get_checkpoint_metadata(config, metadata)

        with self.session_factory() as session:
            cp_row = session.get(
                GraphCheckpoint,
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            if cp_row is None:
                cp_row = GraphCheckpoint(
                    company_id=company_id,
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=parent_checkpoint_id,
                    type=type_,
                    checkpoint=blob,
                    metadata_=meta,
                )
                session.add(cp_row)
            else:
                if cp_row.company_id != company_id:
                    raise PermissionError("checkpoint belongs to another company")
                cp_row.parent_checkpoint_id = parent_checkpoint_id
                cp_row.type = type_
                cp_row.checkpoint = blob
                cp_row.metadata_ = meta

            session.flush()

            # Retention: keep latest 20 checkpoints per (thread_id, checkpoint_ns)
            all_ids = session.scalars(
                select(GraphCheckpoint.checkpoint_id)
                .where(
                    GraphCheckpoint.company_id == company_id,
                    GraphCheckpoint.thread_id == thread_id,
                    GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                )
                .order_by(GraphCheckpoint.checkpoint_id.desc())
            ).all()

            if len(all_ids) > RETENTION_LIMIT:
                to_delete = all_ids[RETENTION_LIMIT:]
                session.execute(
                    delete(GraphWrite).where(
                        GraphWrite.company_id == company_id,
                        GraphWrite.thread_id == thread_id,
                        GraphWrite.checkpoint_ns == checkpoint_ns,
                        GraphWrite.checkpoint_id.in_(to_delete),
                    )
                )
                session.execute(
                    delete(GraphCheckpoint).where(
                        GraphCheckpoint.company_id == company_id,
                        GraphCheckpoint.thread_id == thread_id,
                        GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                        GraphCheckpoint.checkpoint_id.in_(to_delete),
                    )
                )

            session.commit()

        return {
            "configurable": {
                **configurable,
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes linked to a checkpoint."""
        configurable = config.get("configurable", {})
        company_id = configurable.get("company_id")
        if not company_id:
            raise ValueError("company_id is required in config['configurable'] for put_writes")

        thread_id = configurable["thread_id"]
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable["checkpoint_id"]

        with self.session_factory() as session:
            owner = session.scalar(
                select(GraphCheckpoint.company_id).where(
                    GraphCheckpoint.thread_id == thread_id,
                    GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    GraphCheckpoint.checkpoint_id == checkpoint_id,
                )
            )
            if owner is not None and owner != company_id:
                raise PermissionError("checkpoint belongs to another company")
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                type_, blob = self.serde.dumps_typed(value)
                existing = session.get(
                    GraphWrite,
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx),
                )
                if existing is not None:
                    if existing.company_id != company_id:
                        raise PermissionError("checkpoint write belongs to another company")
                    if write_idx >= 0:
                        continue
                    existing.channel = channel
                    existing.type = type_
                    existing.value = blob
                    existing.task_path = task_path
                else:
                    session.add(
                        GraphWrite(
                            company_id=company_id,
                            thread_id=thread_id,
                            checkpoint_ns=checkpoint_ns,
                            checkpoint_id=checkpoint_id,
                            task_id=task_id,
                            idx=write_idx,
                            channel=channel,
                            type=type_,
                            value=blob,
                            task_path=task_path,
                        )
                    )
            session.commit()

    def delete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints and writes associated with a thread ID."""
        with self.session_factory() as session:
            session.execute(delete(GraphWrite).where(GraphWrite.thread_id == thread_id))
            session.execute(delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id))
            session.commit()

    def get_next_version(self, current: str | int | float | None, channel: None = None) -> str:
        """Generate monotonically increasing version identifier."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        elif isinstance(current, float):
            current_v = int(current)
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"


def purge_thread(
    session_factory: sessionmaker[Session] | Callable[[], Session],
    company_id: str,
    thread_id: str,
) -> None:
    """Purge all checkpoints and writes for a thread within a company."""
    with session_factory() as session:
        session.execute(
            delete(GraphWrite).where(
                GraphWrite.company_id == company_id,
                GraphWrite.thread_id == thread_id,
            )
        )
        session.execute(
            delete(GraphCheckpoint).where(
                GraphCheckpoint.company_id == company_id,
                GraphCheckpoint.thread_id == thread_id,
            )
        )
        session.commit()
