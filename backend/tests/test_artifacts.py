from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core.errors import PayloadTooLarge
from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from app.storage.local import LocalFileStorage
from tests.conftest import human
from tests.helpers import api, new_agent, new_task


def upload(
    client, cid, *, name="notes.md", content=b"# Notes\nhello", mime="text/markdown", **data
):
    return client.post(
        api(cid, "/artifacts"),
        files={"file": (name, content, mime)},
        data={"title": "Notes", "kind": "document", **data},
    )


def test_human_upload_and_download_roundtrip(app, client, company):
    cid = company["id"]
    content = b"# Notes\nhello \xe2\x9c\x93"
    r = upload(client, cid, content=content)
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["title"] == "Notes" and art["kind"] == "document" and art["status"] == "draft"
    assert art["mime_type"] == "text/markdown" and art["current_version"] == 1
    assert art["created_by_type"] == "human" and art["task_id"] is None
    [version] = art["versions"]
    assert version["size_bytes"] == len(content)
    assert version["sha256"] == hashlib.sha256(content).hexdigest()
    assert "storage_key" not in version  # internal detail, never exposed

    got = client.get(api(cid, f"/artifacts/{art['id']}/versions/1/content"))
    assert got.status_code == 200 and got.content == content
    assert got.headers["content-type"].startswith("text/markdown")
    assert got.headers["content-disposition"].startswith("inline")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["etag"] == f'"{version["sha256"]}"'
    # content is on disk under company/artifact/version, never in the database
    stored = app.state.settings.artifacts_dir / cid / art["id"] / "1"
    assert stored.read_bytes() == content
    assert client.get(api(cid, f"/artifacts/{art['id']}/versions/2/content")).status_code == 404


def test_json_text_artifact_and_new_versions(client, company):
    cid = company["id"]
    task = new_task(client, cid)
    r = client.post(
        api(cid, "/artifacts"),
        json={"title": "Plan", "kind": "report", "content": "v1", "task_id": task["id"]},
    )
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["task_id"] == task["id"] and art["mime_type"] == "text/markdown"
    r = client.post(
        api(cid, "/artifacts"),
        json={"artifact_id": art["id"], "content": "version two", "note": "edit"},
    )
    assert r.status_code == 201 and r.json()["current_version"] == 2
    assert [v["note"] for v in r.json()["versions"]] == [None, "edit"]
    assert client.get(api(cid, f"/artifacts/{art['id']}/versions/1/content")).text == "v1"
    assert client.get(api(cid, f"/artifacts/{art['id']}/versions/2/content")).text == "version two"
    detail = client.get(api(cid, f"/artifacts/{art['id']}")).json()
    assert detail["current_version"] == 2 and len(detail["versions"]) == 2


def test_download_disposition_is_safe(client, company):
    cid = company["id"]
    cases = {
        "text/plain": "inline",
        "application/json": "inline",
        "text/html": "attachment",  # would execute script on the API origin
        "image/svg+xml": "attachment",
        "application/pdf": "attachment",
        "application/octet-stream": "attachment",
    }
    for mime, expected in cases.items():
        art = upload(client, cid, content=b"<script>1</script>", mime=mime, name="x.bin").json()
        r = client.get(api(cid, f"/artifacts/{art['id']}/versions/1/content"))
        assert r.headers["content-disposition"].startswith(expected), mime
        assert r.headers["x-content-type-options"] == "nosniff"
    # the file name in the header is sanitized
    art = upload(
        client, cid, content=b"x", mime="text/plain", **{"title": '../../etc/passwd"; x=y'}
    ).json()
    cd = client.get(api(cid, f"/artifacts/{art['id']}/versions/1/content")).headers[
        "content-disposition"
    ]
    assert ".." not in cd and "/" not in cd and cd.count('"') == 2


def test_listing_filters_and_search(client, company):
    cid = company["id"]
    task = new_task(client, cid)
    project = client.post(api(cid, "/projects"), json={"name": "P"}).json()
    a = upload(client, cid, title="Quarterly Report", kind="report", task_id=task["id"]).json()
    b = upload(client, cid, title="Logo draft", kind="design", project_id=project["id"]).json()
    c = upload(client, cid, title="100%_sure", kind="data").json()

    def listing(**p):
        return [x["id"] for x in client.get(api(cid, "/artifacts"), params=p).json()["items"]]

    assert listing() == [c["id"], b["id"], a["id"]]  # newest first
    assert listing(task_id=task["id"]) == [a["id"]]
    assert listing(project_id=project["id"]) == [b["id"]]
    assert listing(kind="design") == [b["id"]]
    assert listing(q="report") == [a["id"]]
    assert listing(q="100%") == [c["id"]] and listing(q="%") == [c["id"]]  # wildcards are literal
    assert listing(q="nothing-like-this") == []
    assert listing(status="submitted") == []
    assert client.get(api(cid, "/artifacts"), params={"kind": "bogus"}).status_code == 422
    # artifacts inherit the task's project
    assert client.get(api(cid, f"/artifacts/{a['id']}")).json()["project_id"] is None


def test_status_transitions(client, company):
    cid = company["id"]
    art = upload(client, cid).json()
    url = api(cid, f"/artifacts/{art['id']}")
    assert client.patch(url, json={"status": "submitted"}).json()["status"] == "submitted"
    assert client.patch(url, json={"status": "draft"}).status_code == 409
    assert (
        client.patch(url, json={"status": "rejected", "title": "Renamed"}).json()["title"]
        == "Renamed"
    )
    # a rejected artifact goes back to draft when a new version arrives
    r = client.post(api(cid, "/artifacts"), json={"artifact_id": art["id"], "content": "fixed"})
    assert r.json()["status"] == "draft"
    assert client.patch(url, json={"status": "accepted"}).json()["status"] == "accepted"
    assert client.patch(url, json={"status": "rejected"}).status_code == 409  # final
    assert client.patch(url, json={"status": "nonsense"}).status_code == 422


def test_upload_validation(client, company):
    cid = company["id"]
    assert (
        client.post(
            api(cid, "/artifacts"), data={"title": "x"}, files={"other": ("a", b"b")}
        ).status_code
        == 422
    )
    assert upload(client, cid, kind="bogus").status_code == 422
    assert upload(client, cid, task_id="nope").status_code == 404
    assert upload(client, cid, project_id="nope").status_code == 404
    assert (
        client.post(
            api(cid, "/artifacts"), json={"kind": "document", "content": "no title"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            api(cid, "/artifacts"), json={"title": "t", "content": "c", "surprise": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            api(cid, "/artifacts"), content=b"x", headers={"content-type": "text/plain"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            api(cid, "/artifacts"), json={"artifact_id": "nope", "content": "c"}
        ).status_code
        == 404
    )


def test_size_limit_returns_413_and_stores_nothing(make_settings, tmp_path):
    settings = make_settings(max_upload_bytes=100)
    app = create_app(settings, runtime_registry=RuntimeRegistry())
    with TestClient(app) as c:
        cid = c.post("/api/companies", json={"name": "Tiny"}).json()["id"]
        ok = c.post(
            api(cid, "/artifacts"),
            files={"file": ("a.txt", b"x" * 100, "text/plain")},
            data={"title": "ok"},
        )
        assert ok.status_code == 201
        big = c.post(
            api(cid, "/artifacts"),
            files={"file": ("a.txt", b"x" * 101, "text/plain")},
            data={"title": "big"},
        )
        assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"
        also_big = c.post(api(cid, "/artifacts"), json={"title": "j", "content": "y" * 101})
        assert also_big.status_code == 413
        assert [a["title"] for a in c.get(api(cid, "/artifacts")).json()["items"]] == ["ok"]
        leftovers = [p for p in (settings.artifacts_dir / cid).rglob("*") if p.is_file()]
        assert len(leftovers) == 1  # the failed uploads left no files (not even temp ones)


def test_artifacts_are_tenant_scoped_and_role_checked(
    client, client_as, make_user, add_member, company
):
    cid = company["id"]
    art = upload(client, cid).json()
    other = client.post("/api/companies", json={"name": "Elsewhere"}).json()["id"]
    assert client.get(api(other, f"/artifacts/{art['id']}")).status_code == 404
    assert client.get(api(other, f"/artifacts/{art['id']}/versions/1/content")).status_code == 404
    assert upload(client, other, artifact_id=art["id"]).status_code == 404
    assert client.get(api(other, "/artifacts")).json()["items"] == []
    outsider = client_as(human(make_user("a1@x.test")))
    assert outsider.get(api(cid, f"/artifacts/{art['id']}/versions/1/content")).status_code == 404
    viewer = make_user("a2@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    assert vc.get(api(cid, f"/artifacts/{art['id']}/versions/1/content")).status_code == 200
    assert upload(vc, cid).status_code == 403
    assert (
        vc.patch(api(cid, f"/artifacts/{art['id']}"), json={"status": "accepted"}).status_code
        == 403
    )
    agent = new_agent(client, cid)
    from app.core.principal import Principal, PrincipalType

    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "N", cid))
    assert upload(ac, cid).status_code == 403  # agents use their run (agent API)


def test_artifact_activity_and_events(app, client, company):
    from sqlalchemy import select

    from app.models.run import CompanyEvent

    cid = company["id"]
    art = upload(client, cid).json()
    client.post(api(cid, "/artifacts"), json={"artifact_id": art["id"], "content": "v2"})
    actions = [
        e["action"]
        for e in client.get(api(cid, "/activity"), params={"target_id": art["id"]}).json()["items"]
    ]
    assert actions == ["artifact.version_added", "artifact.created"]
    with app.state.session_factory() as db:
        types = [e.type for e in db.scalars(select(CompanyEvent))]
    assert "artifact.created" in types and "artifact.updated" in types


# ------------------------------------------------------------------ storage unit


def test_local_storage_rejects_path_traversal_and_cleans_up(tmp_path):
    storage = LocalFileStorage(tmp_path / "store")
    for key in ("../escape", "a/../../b", "/abs", "a//b", "a/./b", ".hidden/x", ""):
        with pytest.raises(ValueError):
            storage.put(key, [b"x"])
    stored = storage.put("c1/a1/1", [b"ab", b"cd"])
    assert (stored.size_bytes, stored.sha256) == (4, hashlib.sha256(b"abcd").hexdigest())
    assert b"".join(storage.open("c1/a1/1", chunk_size=3)) == b"abcd"
    assert storage.exists("c1/a1/1") and not storage.exists("c1/a1/2")
    with pytest.raises(PayloadTooLarge):
        storage.put("c1/a1/2", [b"abc", b"def"], max_bytes=5)
    assert not storage.exists("c1/a1/2")
    assert [p.name for p in (tmp_path / "store" / "c1" / "a1").iterdir()] == ["1"]
    storage.delete("c1/a1/1")
    storage.delete("c1/a1/1")  # idempotent
    assert not storage.exists("c1/a1/1")
