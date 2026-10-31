"""M18 Part B: Tests for SqlCheckpointSaver and migration 0020."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import create_engine, inspect, select
from typing_extensions import TypedDict

from alembic import command
from app.engine.graph.checkpointer import SqlCheckpointSaver, purge_thread
from app.models.graph import GraphCheckpoint, GraphWrite
from tests.conftest import alembic_config


def _make_checkpoint(cp_id: str, val: int) -> Checkpoint:
    return {
        "v": 1,
        "id": cp_id,
        "ts": "2026-10-30T15:00:00Z",
        "channel_values": {"count": val},
        "channel_versions": {"count": 1},
        "versions_seen": {},
        "updated_channels": ["count"],
    }


def test_put_get_tuple_roundtrip(app, company):
    cid = company["id"]
    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:test-1"

    config = {"configurable": {"company_id": cid, "thread_id": thread_id}}
    cp = _make_checkpoint("cp-001", 42)
    meta: CheckpointMetadata = {"source": "loop", "step": 1}

    saved_config = saver.put(config, cp, meta, {"count": 1})
    assert saved_config["configurable"]["checkpoint_id"] == "cp-001"
    assert saved_config["configurable"]["thread_id"] == thread_id

    # Retrieve by latest
    retrieved = saver.get_tuple(config)
    assert retrieved is not None
    assert retrieved.checkpoint["id"] == "cp-001"
    assert retrieved.checkpoint["channel_values"] == {"count": 42}
    assert retrieved.metadata["step"] == 1
    assert retrieved.config["configurable"]["checkpoint_id"] == "cp-001"

    # Retrieve by specific checkpoint_id
    specific_config = {
        "configurable": {
            "company_id": cid,
            "thread_id": thread_id,
            "checkpoint_id": "cp-001",
        }
    }
    retrieved_spec = saver.get_tuple(specific_config)
    assert retrieved_spec is not None
    assert retrieved_spec.checkpoint["id"] == "cp-001"


def test_list_ordering_limit_and_before(app, company):
    cid = company["id"]
    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:list-test"

    for i in range(1, 6):
        cp_id = f"cp-00{i}"
        config = {
            "configurable": {
                "company_id": cid,
                "thread_id": thread_id,
                "checkpoint_id": f"cp-00{i - 1}" if i > 1 else None,
            }
        }
        cp = _make_checkpoint(cp_id, i)
        saver.put(config, cp, {"step": i}, {"count": i})

    cfg = {"configurable": {"company_id": cid, "thread_id": thread_id}}

    # Newest first
    all_tuples = list(saver.list(cfg))
    assert len(all_tuples) == 5
    assert [t.checkpoint["id"] for t in all_tuples] == [
        "cp-005",
        "cp-004",
        "cp-003",
        "cp-002",
        "cp-001",
    ]

    # Limit
    limited = list(saver.list(cfg, limit=2))
    assert len(limited) == 2
    assert [t.checkpoint["id"] for t in limited] == ["cp-005", "cp-004"]

    # Before
    before_cfg = {"configurable": {"checkpoint_id": "cp-004"}}
    before_tuples = list(saver.list(cfg, before=before_cfg))
    assert [t.checkpoint["id"] for t in before_tuples] == ["cp-003", "cp-002", "cp-001"]

    # Limit + before
    before_limited = list(saver.list(cfg, before=before_cfg, limit=2))
    assert [t.checkpoint["id"] for t in before_limited] == ["cp-003", "cp-002"]

    # Filter by metadata
    filtered = list(saver.list(cfg, filter={"step": 3}))
    assert len(filtered) == 1
    assert filtered[0].checkpoint["id"] == "cp-003"


def test_put_writes_and_pending_writes(app, company):
    cid = company["id"]
    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:writes-test"

    cfg = {"configurable": {"company_id": cid, "thread_id": thread_id}}
    cp = _make_checkpoint("cp-01", 10)
    saver.put(cfg, cp, {"step": 1}, {"count": 1})

    write_cfg = {
        "configurable": {
            "company_id": cid,
            "thread_id": thread_id,
            "checkpoint_id": "cp-01",
        }
    }
    writes = [("output", "hello world"), ("status", "ok")]
    saver.put_writes(write_cfg, writes, task_id="task-node-1", task_path="path-a")

    # get_tuple should return pending writes in order
    tup = saver.get_tuple(write_cfg)
    assert tup is not None
    assert tup.pending_writes is not None
    assert len(tup.pending_writes) == 2
    assert tup.pending_writes[0] == ("task-node-1", "output", "hello world")
    assert tup.pending_writes[1] == ("task-node-1", "status", "ok")


def test_tenancy_isolation_and_validation(app, client: TestClient, company):
    cid_a = company["id"]
    resp = client.post("/api/companies", json={"name": "Company B", "mission": "Other"})
    assert resp.status_code == 201
    cid_b = resp.json()["id"]

    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:tenant-test"

    # Put without company_id raises ValueError
    no_company_cfg = {"configurable": {"thread_id": thread_id}}
    cp = _make_checkpoint("cp-01", 1)
    with pytest.raises(ValueError, match="company_id is required"):
        saver.put(no_company_cfg, cp, {}, {})

    # put_writes without company_id raises ValueError
    with pytest.raises(ValueError, match="company_id is required"):
        saver.put_writes(
            {"configurable": {"thread_id": thread_id, "checkpoint_id": "cp-01"}},
            [("out", "val")],
            task_id="t1",
        )

    # Put checkpoint under Company A
    cfg_a = {"configurable": {"company_id": cid_a, "thread_id": thread_id}}
    saver.put(cfg_a, cp, {"owner": "A"}, {})

    # Company A can read it
    assert saver.get_tuple(cfg_a) is not None
    assert len(list(saver.list(cfg_a))) == 1

    # Company B CANNOT read Company A's thread (get_tuple returns None, list returns empty)
    cfg_b = {"configurable": {"company_id": cid_b, "thread_id": thread_id}}
    assert saver.get_tuple(cfg_b) is None
    assert list(saver.list(cfg_b)) == []

    # Even if specific checkpoint_id is requested by Company B, it returns None
    cfg_b_specific = {
        "configurable": {
            "company_id": cid_b,
            "thread_id": thread_id,
            "checkpoint_id": "cp-01",
        }
    }
    assert saver.get_tuple(cfg_b_specific) is None


def test_tenancy_put_cannot_overwrite_other_company(app, client: TestClient, company):
    cid_a = company["id"]
    cid_b = client.post("/api/companies", json={"name": "Company B2", "mission": "Other"}).json()[
        "id"
    ]
    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:shared-id"
    cfg_a = {"configurable": {"company_id": cid_a, "thread_id": thread_id}}
    cfg_b = {"configurable": {"company_id": cid_b, "thread_id": thread_id}}
    saver.put(cfg_a, _make_checkpoint("cp-01", 1), {"owner": "A"}, {})

    with pytest.raises(PermissionError):
        saver.put(cfg_b, _make_checkpoint("cp-01", 2), {"owner": "B"}, {})
    with pytest.raises(PermissionError):
        saver.put_writes(
            {"configurable": {**cfg_b["configurable"], "checkpoint_id": "cp-01"}},
            [("__error__", "x")],
            task_id="t1",
        )
        saver.put_writes(
            {"configurable": {**cfg_b["configurable"], "checkpoint_id": "cp-01"}},
            [("__error__", "y")],
            task_id="t1",
        )

    saver.put_writes(
        {"configurable": {**cfg_a["configurable"], "checkpoint_id": "cp-01"}},
        [("__error__", "a")],
        task_id="t2",
    )
    a = saver.get_tuple(cfg_a)
    assert a is not None and a.metadata["owner"] == "A"


def test_retention_keeps_twenty(app, company):
    cid = company["id"]
    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:retention-test"

    for i in range(1, 26):
        cp_id = f"cp-{i:03d}"
        cfg = {
            "configurable": {
                "company_id": cid,
                "thread_id": thread_id,
                "checkpoint_id": f"cp-{(i - 1):03d}" if i > 1 else None,
            }
        }
        cp = _make_checkpoint(cp_id, i)
        saver.put(cfg, cp, {"step": i}, {"count": i})
        saver.put_writes(
            {
                "configurable": {
                    "company_id": cid,
                    "thread_id": thread_id,
                    "checkpoint_id": cp_id,
                }
            },
            [("chan", f"write-{i}")],
            task_id=f"task-{i}",
        )

    # Verify that only the latest 20 checkpoints exist in DB
    cfg = {"configurable": {"company_id": cid, "thread_id": thread_id}}
    items = list(saver.list(cfg))
    assert len(items) == 20

    # The kept IDs must be cp-025 down to cp-006 (cp-001 through cp-005 pruned)
    kept_ids = [t.checkpoint["id"] for t in items]
    assert kept_ids[0] == "cp-025"
    assert kept_ids[-1] == "cp-006"
    assert "cp-005" not in kept_ids
    assert "cp-001" not in kept_ids

    # Check database rows for checkpoints and writes: old ones are deleted too
    with app.state.session_factory() as session:
        checkpoints = session.scalars(
            select(GraphCheckpoint.checkpoint_id).where(GraphCheckpoint.thread_id == thread_id)
        ).all()
        assert len(checkpoints) == 20
        assert set(checkpoints) == set(kept_ids)

        writes = session.scalars(
            select(GraphWrite.checkpoint_id).where(GraphWrite.thread_id == thread_id)
        ).all()
        assert len(writes) == 20
        assert set(writes) == set(kept_ids)


def test_delete_thread_and_purge_thread(app, client: TestClient, company):
    cid_a = company["id"]
    resp = client.post("/api/companies", json={"name": "Company 2", "mission": "Test"})
    cid_b = resp.json()["id"]

    saver = SqlCheckpointSaver(app.state.session_factory)
    thread_1 = "task:del-1"
    thread_2 = "task:del-2"

    cfg_1 = {"configurable": {"company_id": cid_a, "thread_id": thread_1}}
    cfg_2 = {"configurable": {"company_id": cid_b, "thread_id": thread_2}}

    saver.put(cfg_1, _make_checkpoint("cp-1", 1), {}, {})
    saver.put_writes(
        {"configurable": {"company_id": cid_a, "thread_id": thread_1, "checkpoint_id": "cp-1"}},
        [("c", "w1")],
        task_id="t1",
    )
    saver.put(cfg_2, _make_checkpoint("cp-2", 2), {}, {})
    saver.put_writes(
        {"configurable": {"company_id": cid_b, "thread_id": thread_2, "checkpoint_id": "cp-2"}},
        [("c", "w2")],
        task_id="t2",
    )

    # delete_thread deletes thread_1
    saver.delete_thread(thread_1)
    assert saver.get_tuple(cfg_1) is None
    # thread_2 is unaffected
    assert saver.get_tuple(cfg_2) is not None

    # purge_thread deletes thread_2 with company check
    purge_thread(app.state.session_factory, cid_b, thread_2)
    assert saver.get_tuple(cfg_2) is None


def test_end_to_end_stategraph_interrupt_and_restart(app, company):
    cid = company["id"]
    saver1 = SqlCheckpointSaver(app.state.session_factory)
    thread_id = "task:e2e-interrupt"

    class GraphState(TypedDict):
        text: str

    def first_node(state: GraphState) -> dict:
        return {"text": state["text"] + " -> first"}

    def second_node(state: GraphState) -> dict:
        resume_val = interrupt("Waiting for approval or answer")
        return {"text": state["text"] + f" -> second({resume_val})"}

    builder = StateGraph(GraphState)
    builder.add_node("first", first_node)
    builder.add_node("second", second_node)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    graph1 = builder.compile(checkpointer=saver1)
    config = {"configurable": {"company_id": cid, "thread_id": thread_id}}

    # Initial invoke pauses at interrupt in second node
    res1 = graph1.invoke({"text": "init"}, config)
    assert "__interrupt__" in res1
    assert res1["text"] == "init -> first"
    state1 = graph1.get_state(config)
    assert state1.next == ("second",)

    # Process restart: instantiate a NEW SqlCheckpointSaver and re-compile graph
    saver2 = SqlCheckpointSaver(app.state.session_factory)
    graph2 = builder.compile(checkpointer=saver2)

    # Resume with Command(resume="yes")
    res2 = graph2.invoke(Command(resume="yes"), config)
    assert res2["text"] == "init -> first -> second(yes)"

    state2 = graph2.get_state(config)
    assert state2.next == ()


def test_0020_migration_upgrades_and_downgrades(tmp_path):
    url = f"sqlite:///{tmp_path / 'm18.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0019")
    engine = create_engine(url)
    insp = inspect(engine)
    assert not {"graph_checkpoints", "graph_writes"} & set(insp.get_table_names())

    command.upgrade(cfg, "0020")
    insp = inspect(engine)
    assert {"graph_checkpoints", "graph_writes"} <= set(insp.get_table_names())
    indexes = {i["name"] for i in inspect(engine).get_indexes("graph_checkpoints")}
    assert "ix_graph_checkpoints_company_id_thread_id" in indexes

    command.downgrade(cfg, "0019")
    insp = inspect(engine)
    assert not {"graph_checkpoints", "graph_writes"} & set(insp.get_table_names())

    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert {"graph_checkpoints", "graph_writes"} <= set(insp.get_table_names())
    engine.dispose()
