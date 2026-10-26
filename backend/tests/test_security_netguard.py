"""SSRF guard for owner-supplied URLs (docs/security.md G3): classification, the URL check, the
pinned transport (DNS rebinding) and the model-provider API."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import select

from app.core import netguard
from app.core.netguard import BlockedDestination, GuardedTransport, Policy, UnsafeUrl
from app.models.audit import AuditEvent
from app.models.guardrails import SystemState
from tests.conftest import human
from tests.helpers import api

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    """A fake resolver: ``dns.table[host] = [ips]``; every call is recorded in ``dns.calls``."""

    class Fake:
        def __init__(self) -> None:
            self.table: dict[str, list[str]] = {}
            self.calls: list[str] = []

        def __call__(self, host: str, port: int) -> list[str]:
            self.calls.append(host)
            try:
                return list(self.table[host])
            except KeyError:
                raise OSError(f"cannot resolve {host}") from None

    fake = Fake()
    monkeypatch.setattr(netguard, "_resolver", fake)
    return fake


# ------------------------------------------------------------------ classification


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("93.184.216.34", "public"),
        ("2606:4700:4700::1111", "public"),
        ("127.0.0.1", "loopback"),
        ("127.5.5.5", "loopback"),
        ("::1", "loopback"),
        ("10.0.0.5", "private"),
        ("172.16.0.1", "private"),
        ("172.31.255.255", "private"),
        ("172.32.0.1", "public"),
        ("192.168.1.20", "private"),
        ("100.64.0.7", "private"),  # CGNAT / Tailscale
        ("fd12:3456::1", "private"),  # IPv6 ULA
        ("169.254.169.254", "metadata"),
        ("169.254.170.2", "metadata"),
        ("fd00:ec2::254", "metadata"),
        ("100.100.100.200", "metadata"),
        ("169.254.10.10", "link_local"),
        ("fe80::1", "link_local"),
        ("0.0.0.0", "unspecified"),
        ("::", "unspecified"),
        ("224.0.0.1", "multicast"),
        ("ff02::1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("255.255.255.255", "reserved"),
        ("0.1.2.3", "reserved"),
        ("192.0.2.10", "reserved"),  # documentation range
        ("198.18.0.1", "reserved"),  # benchmarking
        ("::ffff:169.254.169.254", "metadata"),  # IPv4-mapped
        ("::ffff:127.0.0.1", "loopback"),
        ("::ffff:10.1.1.1", "private"),
        ("64:ff9b::a9fe:a9fe", "metadata"),  # NAT64 carrying 169.254.169.254
        ("2002:a9fe:a9fe::1", "metadata"),  # 6to4 carrying 169.254.169.254
    ],
)
def test_classification(ip, expected):
    assert netguard.classify_ip(ip) == expected


# ------------------------------------------------------------------ check_url


def refusal(url, **kw) -> UnsafeUrl:
    with pytest.raises(UnsafeUrl) as info:
        netguard.check_url(url, **kw)
    return info.value


def test_scheme_credentials_and_syntax():
    assert refusal("ftp://example.com").reason == "scheme"
    assert refusal("file:///etc/passwd").reason == "scheme"
    assert refusal("//example.com/x").reason == "scheme"
    assert refusal("http://user:hunter2@example.com/v1").reason == "credentials"
    assert "hunter2" not in refusal("http://user:hunter2@example.com/v1").message
    assert refusal("http://example.com:notaport/").reason == "invalid"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254:80/",
        "http://[::ffff:169.254.169.254]/",
        "http://[fd00:ec2::254]/",
        "http://169.254.1.1/",
        "http://[fe80::1]/",
        "http://0.0.0.0:8080/",
        "http://[::]/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://192.0.2.1/",
    ],
)
def test_never_allowed_even_when_loopback_and_private_are(url):
    err = refusal(url, allow_loopback=True, allow_private=True)
    assert err.status_code == 422 and err.code == "unsafe_url"
    assert err.details[0]["can_allow"] is False


def test_metadata_via_a_hostname_and_via_one_of_several_records(dns):
    dns.table["metadata.evil.test"] = ["169.254.169.254"]
    dns.table["mixed.evil.test"] = [PUBLIC, "169.254.169.254"]  # one bad record is enough
    for host in ("metadata.evil.test", "mixed.evil.test"):
        err = refusal(f"https://{host}/v1", allow_loopback=True, allow_private=True)
        assert err.reason in ("metadata", "link_local")


def test_unusual_spellings_of_loopback_are_judged_by_what_they_resolve_to(monkeypatch):
    monkeypatch.setattr(netguard, "_resolver", netguard.system_resolver)
    checked = 0
    for spelling in ("2130706433", "0x7f.1", "0177.0.0.1", "127.1"):
        try:
            resolved = netguard.system_resolver(spelling, 80)
        except OSError:
            continue  # this platform's resolver does not accept that spelling
        if {netguard.classify_ip(ip) for ip in resolved} != {"loopback"}:
            continue  # it means something else here (e.g. 0177 read as decimal): not a bypass
        assert refusal(f"http://{spelling}/").reason == "loopback"
        netguard.check_url(f"http://{spelling}/", allow_loopback=True)
        checked += 1
    if not checked:
        pytest.skip("this platform's resolver accepts no alternative IPv4 spelling")


def test_loopback_and_private_need_the_matching_permission(dns):
    dns.table["lan.test"] = ["192.168.1.20"]
    dns.table["localhost.test"] = ["127.0.0.1"]
    assert refusal("http://127.0.0.1:11434").reason == "loopback"
    netguard.check_url("http://127.0.0.1:11434", allow_loopback=True)
    netguard.check_url("http://[::1]:11434", allow_loopback=True)
    err = refusal("http://lan.test:8000/v1", allow_loopback=True)
    assert err.reason == "private_network" and err.details[0]["can_allow"] is True
    assert "allow private network" in err.message
    netguard.check_url("http://lan.test:8000/v1", allow_loopback=True, allow_private=True)
    # private permission does not imply loopback
    assert refusal("http://localhost.test/", allow_private=True).reason == "loopback"


def test_public_addresses_pass_and_resolution_rules(dns):
    dns.table["api.example.test"] = [PUBLIC]
    checked = netguard.check_url("https://api.example.test/v1")
    assert checked.addresses == (PUBLIC,) and checked.port == 443 and checked.host
    assert refusal("https://nope.example.test/").reason == "unresolvable"
    # saving a URL whose name does not resolve yet is tolerated; using it is not
    assert (
        netguard.check_url("https://nope.example.test/", require_resolution=False).addresses == ()
    )


def test_provider_policy_by_kind():
    assert netguard.provider_policy("ollama") == Policy(True, False)
    assert netguard.provider_policy("openai_compatible", True) == Policy(True, True)
    for kind in ("openrouter", "anthropic", "openai"):
        assert netguard.provider_policy(kind, True) == netguard.PUBLIC_ONLY


# ------------------------------------------------------------------ the pinned transport


def guarded_client(policy, handler):
    return httpx.Client(
        transport=GuardedTransport(policy, inner=httpx.MockTransport(handler)), trust_env=False
    )


def test_the_request_connects_to_the_validated_ip_and_keeps_name_and_sni(dns):
    dns.table["models.example.test"] = [PUBLIC]
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["header"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    with guarded_client(netguard.PUBLIC_ONLY, handler) as http:
        assert http.get("https://models.example.test/v1/models").status_code == 200
    assert seen == {
        "host": PUBLIC,
        "header": "models.example.test",
        "sni": "models.example.test",
        "path": "/v1/models",
    }
    assert dns.calls == ["models.example.test"]  # one lookup per request: nothing to race


def test_dns_rebinding_is_refused_and_no_connection_is_attempted(dns):
    """The name is public when it is saved and 169.254.169.254 when it is used."""
    dns.table["rebind.example.test"] = [PUBLIC]
    netguard.check_url("https://rebind.example.test/v1", require_resolution=False)  # save time
    dns.table["rebind.example.test"] = ["169.254.169.254"]  # the attacker's DNS answers differently
    connected = []

    def handler(request: httpx.Request) -> httpx.Response:
        connected.append(request)
        return httpx.Response(200)

    with (
        guarded_client(netguard.PUBLIC_ONLY, handler) as http,
        pytest.raises(BlockedDestination) as info,
    ):
        http.get("https://rebind.example.test/latest/meta-data/")
    assert info.value.reason == "metadata" and connected == []


def test_a_name_with_one_public_and_one_internal_record_is_refused(dns):
    dns.table["split.example.test"] = [PUBLIC, "10.0.0.5"]
    with (
        guarded_client(netguard.PUBLIC_ONLY, lambda r: httpx.Response(200)) as http,
        pytest.raises(BlockedDestination),
    ):
        http.get("https://split.example.test/")


def test_redirects_are_never_followed_to_an_unchecked_destination(dns):
    dns.table["redir.example.test"] = [PUBLIC]
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    transport = GuardedTransport(netguard.PUBLIC_ONLY, inner=httpx.MockTransport(handler))
    # even a client that follows redirects re-enters the guard for the new destination
    with (
        httpx.Client(transport=transport, follow_redirects=True, trust_env=False) as http,
        pytest.raises(BlockedDestination),
    ):
        http.get("https://redir.example.test/")
    assert hits == [PUBLIC]


def test_an_unreachable_first_address_falls_back_to_the_next_validated_one(dns):
    dns.table["two.example.test"] = ["2606:4700:4700::1111", PUBLIC, "93.184.216.35"]
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if request.url.host == PUBLIC:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    with guarded_client(netguard.PUBLIC_ONLY, handler) as http:
        assert http.get("https://two.example.test/").status_code == 200
    assert tried == [PUBLIC, "93.184.216.35"]  # IPv4 first, only validated addresses


class _Echo(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.headers.get("Host", ""))
        body = b'{"hello":"world"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_a_real_loopback_server_is_reachable_for_local_kinds_only():
    _Echo.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        url = f"http://localhost:{port}/api/tags"
        with httpx.Client(transport=GuardedTransport(netguard.LOCAL_MODEL)) as http:
            assert http.get(url).json() == {"hello": "world"}
        assert _Echo.hits == [f"localhost:{port}"]  # the original name stays in Host
        _Echo.hits.clear()
        with (
            httpx.Client(transport=GuardedTransport(netguard.PUBLIC_ONLY)) as http,
            pytest.raises(BlockedDestination),
        ):
            http.get(url)
        assert _Echo.hits == []  # blocked before any connection
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ the model-provider API


def provider_body(**kw):
    return {"name": "P", "kind": "openai_compatible", "default_model": "m", **kw}


def create(client, cid, **kw):
    return client.post(api(cid, "/model-providers"), json=provider_body(**kw))


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/v1",
        "http://[::ffff:169.254.169.254]/v1",
        "http://[fe80::1]/v1",
        "http://0.0.0.0:8000/v1",
        "http://224.0.0.1/v1",
    ],
)
def test_the_api_refuses_metadata_link_local_and_reserved_targets(client, company, url):
    r = create(client, company["id"], base_url=url)
    assert r.status_code == 422, r.text
    body = r.json()["error"]
    assert body["code"] == "unsafe_url" and body["details"][0]["loc"] == ["body", "base_url"]
    assert body["details"][0]["can_allow"] is False
    # also with the private-network opt-in
    assert (
        create(client, company["id"], base_url=url, allow_private_network=True).status_code == 422
    )
    # and for Ollama
    assert (
        client.post(
            api(company["id"], "/model-providers"),
            json={"name": "o", "kind": "ollama", "default_model": "m", "base_url": url},
        ).status_code
        == 422
    )


def test_local_models_on_loopback_stay_allowed(client, company):
    cid = company["id"]
    r = create(client, cid, name="vllm", base_url="http://127.0.0.1:8000/v1")
    assert r.status_code == 201 and r.json()["allow_private_network"] is False
    r = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "o",
            "kind": "ollama",
            "default_model": "m",
            "base_url": "http://[::1]:11434",
        },
    )
    assert r.status_code == 201


def test_private_network_needs_the_explicit_opt_in(app, client, company, dns):
    cid = company["id"]
    dns.table["gpu-box.lan"] = ["192.168.1.20"]
    url = "http://gpu-box.lan:8000/v1"
    r = create(client, cid, base_url=url)
    assert r.status_code == 422
    detail = r.json()["error"]["details"][0]
    assert detail["reason"] == "private_network" and detail["can_allow"] is True
    r = create(client, cid, base_url="http://10.1.2.3:8000/v1")
    assert r.status_code == 422
    r = create(client, cid, base_url=url, allow_private_network=True)
    assert r.status_code == 201 and r.json()["allow_private_network"] is True
    pid = r.json()["id"]
    assert client.get(api(cid, f"/model-providers/{pid}")).json()["allow_private_network"] is True
    listed = client.get(api(cid, "/model-providers")).json()["items"]
    assert [p["allow_private_network"] for p in listed] == [True]
    # the opt-in is audited
    with app.state.session_factory() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "model_provider.created", AuditEvent.target_id == pid
            )
        )
    assert event is not None and event.data["allow_private_network"] is True


def test_turning_the_opt_in_off_or_changing_the_url_is_revalidated(client, company, dns):
    cid = company["id"]
    dns.table["gpu-box.lan"] = ["192.168.1.20"]
    pid = create(
        client, cid, base_url="http://gpu-box.lan:8000/v1", allow_private_network=True
    ).json()["id"]
    url = api(cid, f"/model-providers/{pid}")
    # withdrawing the opt-in while the URL is private is refused (it would silently break the provider)
    assert client.patch(url, json={"allow_private_network": False}).status_code == 422
    # a new private URL under the opt-in is fine; the metadata address never is
    assert client.patch(url, json={"base_url": "http://10.0.0.9:8000/v1"}).status_code == 200
    r = client.patch(url, json={"base_url": "http://169.254.169.254/v1"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsafe_url"
    # switching to a loopback URL and then withdrawing the opt-in works
    assert (
        client.patch(
            url, json={"base_url": "http://127.0.0.1:8000/v1", "allow_private_network": False}
        ).json()["allow_private_network"]
        is False
    )
    assert client.patch(url, json={"allow_private_network": None}).status_code == 422


def test_cloud_kinds_must_be_public_and_cannot_opt_in(client, company, dns):
    cid = company["id"]
    dns.table["internal.example.test"] = ["10.0.0.5"]
    cred = client.post(api(cid, "/credentials"), json={"name": "k", "value": "sk-0123456789abcdef"})
    base = {"kind": "openai", "default_model": "m", "credential_id": cred.json()["id"]}
    for i, url in enumerate(
        ["http://127.0.0.1:9000/v1", "https://internal.example.test/v1", "http://10.0.0.5/v1"]
    ):
        r = client.post(
            api(cid, "/model-providers"), json={**base, "name": f"c{i}", "base_url": url}
        )
        assert r.status_code == 422, url
    r = client.post(
        api(cid, "/model-providers"),
        json={
            **base,
            "name": "opt",
            "base_url": "https://api.openai.com/v1",
            "allow_private_network": True,
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["loc"] == ["body", "allow_private_network"]
    ok = client.post(
        api(cid, "/model-providers"),
        json={**base, "name": "fine", "base_url": "https://api.openai.com/v1"},
    )
    assert ok.status_code == 201


def test_detect_refuses_dangerous_base_urls(client, company):
    cid = company["id"]
    for url in ("http://169.254.169.254", "http://[fe80::1]:11434"):
        r = client.post(api(cid, "/model-providers/detect"), json={"base_url": url})
        assert r.status_code == 422 and r.json()["error"]["code"] == "unsafe_url", url
    r = client.post(api(cid, "/model-providers/detect"), json={"base_url": "http://10.0.0.7:11434"})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["can_allow"] is True


def _models_server(monkeypatch, handler):
    """Make the guard's real transport talk to a mock (what would be an actual connection)."""
    monkeypatch.setattr(
        netguard.httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(handler), raising=True
    )


def test_a_test_run_repeats_the_check_and_defeats_rebinding(client, company, dns, monkeypatch):
    cid = company["id"]
    connected = []
    _models_server(monkeypatch, lambda req: connected.append(req) or httpx.Response(200, json={}))
    dns.table["models.example.test"] = [PUBLIC]
    p = create(client, cid, base_url="https://models.example.test/v1").json()
    dns.table["models.example.test"] = ["169.254.169.254"]  # rebinding after the URL was accepted
    done = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()
    assert done["last_test"]["ok"] is False and done["last_test"]["status"] == "error"
    assert "blocked" in done["last_test"]["message"]
    assert connected == []
    listing = client.get(api(cid, f"/model-providers/{p['id']}/models")).json()
    assert listing["available"] is False and "blocked" in listing["detail"]


def test_a_private_provider_is_pinned_to_the_resolved_address(client, company, dns, monkeypatch):
    cid = company["id"]
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers["host"]))
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    _models_server(monkeypatch, handler)
    dns.table["gpu-box.lan"] = ["192.168.1.20"]
    p = create(
        client, cid, base_url="http://gpu-box.lan:8000/v1", allow_private_network=True
    ).json()
    listing = client.get(api(cid, f"/model-providers/{p['id']}/models")).json()
    assert [m["id"] for m in listing["items"]] == ["m"]
    assert seen == [("192.168.1.20", "gpu-box.lan:8000")]
    # the same host without the stored opt-in is refused when it is used
    seen.clear()
    dns.table["other-box.lan"] = [PUBLIC]
    plain = create(client, cid, name="plain", base_url="http://other-box.lan:8000/v1").json()
    dns.table["other-box.lan"] = ["192.168.1.21"]
    listing = client.get(api(cid, f"/model-providers/{plain['id']}/models")).json()
    assert listing["available"] is False and "blocked" in listing["detail"] and seen == []


def test_a_single_test_can_opt_in_without_saving_it(client, company, dns, monkeypatch):
    cid = company["id"]
    reached = []
    _models_server(
        monkeypatch, lambda req: reached.append(req.url.host) or httpx.Response(200, json={})
    )
    dns.table["flip.example.test"] = [PUBLIC]
    p = create(client, cid, base_url="http://flip.example.test:8000/v1").json()
    dns.table["flip.example.test"] = ["172.20.0.4"]  # moved onto the private network
    blocked = client.post(api(cid, f"/model-providers/{p['id']}/test")).json()
    assert "blocked" in blocked["last_test"]["message"] and reached == []
    client.post(api(cid, f"/model-providers/{p['id']}/test"), json={"allow_private_network": True})
    assert "172.20.0.4" in reached
    assert (
        client.get(api(cid, f"/model-providers/{p['id']}")).json()["allow_private_network"] is False
    )


def test_the_opt_in_is_owner_admin_only_and_cleaned_up_on_delete(
    app, client, client_as, make_user, add_member, company, dns
):
    cid = company["id"]
    dns.table["gpu-box.lan"] = ["192.168.1.20"]
    pid = create(
        client,
        cid,
        base_url="http://gpu-box.lan:8000/v1",
        allow_private_network=True,
        enabled=False,  # a provider no agent uses, so it can be deleted
    ).json()["id"]
    viewer_user = make_user("v@example.com")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    assert (
        viewer.patch(
            api(cid, f"/model-providers/{pid}"), json={"allow_private_network": False}
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            api(cid, "/model-providers"), json=provider_body(name="x", allow_private_network=True)
        ).status_code
        == 403
    )
    with app.state.session_factory() as db:
        assert db.scalar(select(SystemState).where(SystemState.key == f"pn:{pid}")) is not None
    assert client.delete(api(cid, f"/model-providers/{pid}")).status_code == 204
    with app.state.session_factory() as db:
        assert db.scalar(select(SystemState).where(SystemState.key == f"pn:{pid}")) is None


def test_system_state_key_fits_the_column():
    assert len(f"pn:{'x' * 36}") <= 40
