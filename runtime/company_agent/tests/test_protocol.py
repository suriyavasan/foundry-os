"""Event writer: sequencing, single terminal event, redaction."""

from __future__ import annotations

import io
import json
import threading

import pytest

from company_agent.protocol import EventWriter, Redactor, truncate


def lines(buf: io.StringIO):
    return [json.loads(l) for l in buf.getvalue().splitlines()]


def test_seq_starts_at_one_and_is_strictly_increasing_under_threads():
    buf = io.StringIO()
    w = EventWriter(buf)

    def spam(n):
        for i in range(50):
            w.emit("progress", {"message": f"{n}-{i}"})

    threads = [threading.Thread(target=spam, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    seqs = [e["seq"] for e in lines(buf)]
    assert seqs == list(range(1, 401))


def test_event_shape_and_timestamp():
    buf = io.StringIO()
    EventWriter(buf).emit("message", {"text": "hi"})
    (event,) = lines(buf)
    assert set(event) == {"seq", "type", "ts", "payload"}
    assert event["ts"].endswith("Z") and event["payload"] == {"text": "hi"}


@pytest.mark.parametrize("terminal", ["run.completed", "run.failed", "run.cancelled"])
def test_exactly_one_terminal_event_and_nothing_after(terminal):
    buf = io.StringIO()
    w = EventWriter(buf)
    assert w.emit("run.started", {})
    assert w.emit(terminal, {"summary": "x"})
    assert not w.emit("run.failed", {"summary": "second"})
    assert not w.emit("message", {"text": "late"})
    types = [e["type"] for e in lines(buf)]
    assert types == ["run.started", terminal]
    assert w.terminal_type == terminal


def test_unknown_event_type_is_a_programming_error():
    with pytest.raises(ValueError):
        EventWriter(io.StringIO()).emit("run.exploded", {})


def test_known_secrets_are_redacted_everywhere_in_the_payload():
    buf = io.StringIO()
    w = EventWriter(buf, secrets=["run-token-abcdef123456", "sk-model-key-0123456789"])
    w.emit("tool.completed", {
        "name": "t", "result_preview": "token run-token-abcdef123456 and key sk-model-key-0123456789",
        "nested": {"list": ["x run-token-abcdef123456 y"], "run-token-abcdef123456": 1},
    })
    raw = buf.getvalue()
    assert "run-token-abcdef123456" not in raw and "sk-model-key-0123456789" not in raw
    assert raw.count("[REDACTED]") == 4


def test_secrets_added_late_are_honoured_and_short_ones_ignored():
    buf = io.StringIO()
    w = EventWriter(buf)
    w.add_secrets(["short", "late-secret-value-1"])
    w.emit("message", {"text": "short text late-secret-value-1"})
    out = lines(buf)[0]["payload"]["text"]
    assert "late-secret-value-1" not in out and "short text" in out


def test_credential_shapes_redacted_without_knowing_the_secret():
    r = Redactor()
    assert "abcdefgh12345678" not in r.text("Authorization: Bearer abcdefgh12345678")
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA" not in r.text("key=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA")
    assert "hunter22222" not in r.text('{"password": "hunter22222"}')
    assert r.text("ordinary text about tokens and keys") == "ordinary text about tokens and keys"


def test_long_strings_truncated_and_non_json_values_stringified():
    buf = io.StringIO()
    w = EventWriter(buf)
    w.emit("message", {"text": "x" * 50_000, "obj": object(), "n": 3, "none": None, "ok": True})
    p = lines(buf)[0]["payload"]
    assert len(p["text"]) <= 20_000 and p["text"].endswith("[truncated]")
    assert isinstance(p["obj"], str) and p["n"] == 3 and p["none"] is None and p["ok"] is True
    assert len(w.preview("y" * 5000)) <= 2000
    assert truncate("short", 100) == "short"


def test_broken_pipe_marks_writer_broken_and_calls_back():
    class Gone(io.StringIO):
        def write(self, s):
            raise BrokenPipeError

    hits = []
    w = EventWriter(Gone(), on_broken=lambda: hits.append(1))
    assert not w.emit("progress", {})
    assert w.broken and hits == [1]
    assert not w.emit("progress", {})
