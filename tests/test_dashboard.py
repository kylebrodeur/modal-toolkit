"""Fleet dashboard contract tests: session auth, adapter ring, stats
aggregation, billing archive, config seed.

The contracts that matter: unauthorized reads 401; the card grid sees
exactly the configured+enabled packages; adapters never raise; billing
archival dedupes by key; the seed honors enabled flags.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit.dashboard.adapters import Probe
from toolkit.dashboard.adapters import finetune as finetune_adapter
from toolkit.dashboard.auth import Session, dashboard_login_html
from toolkit.dashboard.billing import archive_billing
from toolkit.dashboard.config_seed import dashboard_config


class _Item:
    """Billing-report row stand-in (the attributes archive_billing reads)."""

    def __init__(self, interval_start: str, cost: float, resources: dict[str, float]):
        self.interval_start = interval_start
        self.cost = cost
        self.cost_by_resource = resources


def test_session_value_is_issued_hmac_pair():
    session = Session(token="test-token-123")
    issued, _, sig = session.value().partition(".")
    assert issued.isdigit()
    assert len(sig) == 64  # sha256 hex


def test_session_valid_requires_cookie_and_max_age():
    session = Session(token="test-token-123", max_age=10)
    value = session.value()

    cookies = SimpleNamespace(cookies={"modal_toolkit_dashboard_session": value})

    assert session.valid(cookies)  # just issued
    old = f"{int(time.time()) - 100}.{value.split('.')[-1]}"

    old_cookies = SimpleNamespace(cookies={"modal_toolkit_dashboard_session": old})

    assert not session.valid(old_cookies)  # older than max_age

    no_cookies = SimpleNamespace(cookies={})

    assert not session.valid(no_cookies)


def test_login_html_has_hash_fragment_autologin():
    html = dashboard_login_html()
    assert "#key=" in html
    assert 'action="/_toolkit/logout"' not in html
    assert "history.replaceState" in html


def test_probe_from_exception_truncates():
    try:
        raise RuntimeError("x" * 500)
    except RuntimeError as exc:
        probe = Probe.from_exception(exc := exc, 7)
    assert probe.ok is False
    assert len(probe.error) <= 200
    assert probe.took_ms == 7


def test_finetune_adapter_never_probes():
    probe = finetune_adapter.ADAPTER.health({})
    assert probe.ok is True
    assert probe.data["status"] == "job-package"


def test_archive_billing_dedupes_and_appends(tmp_path):
    target = tmp_path / "billing-history.jsonl"
    day = _Item("2026-10-08 00:00:00+00:00", 1.25, {"gpu-h200": 1.25})
    hour = _Item("2026-10-08T10:00:00+00:00", 0.5, {"gpu-h200": 0.5})
    archive_billing([day], [hour], target)
    first = target.read_text().splitlines()
    assert len(first) == 2
    # Re-archive the SAME rows: deduped to zero new lines
    archive_billing([day], [hour], target)
    assert len(target.read_text().splitlines()) == 2
    # A NEW hour appends one
    hour2 = _Item("2026-10-08T11:00:00+00:00", 0.75, {})
    archive_billing([], [hour2], target)
    assert len(target.read_text().splitlines()) == 3
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert rows[0]["kind"] == "day" and rows[0]["usd"] == 1.25
    assert rows[1]["kind"] == "hour" and rows[1]["key"].endswith("10:00:00+00:00")


def test_dashboard_config_seeds_packages_with_enabled_flags():
    body = dashboard_config(
        {
            "embedding": {"base_url": "https://e.example.com", "token": "t1"},
            "inference": {"base_url": "https://i.example.com", "token": "t2", "enabled": False},
            "vision": {},  # no base_url -> disabled by default
            "finetune": {"app_name": "modal-finetune-server"},
        }
    )
    pkgs = body["packages"]
    assert pkgs["embedding"]["enabled"] is True
    assert pkgs["inference"]["enabled"] is False
    assert pkgs["vision"]["enabled"] is False
    assert pkgs["finetune"]["enabled"] is False  # no base_url
    assert pkgs["finetune"]["app_name"] == "modal-finetune-server"


def test_dashboard_config_passes_through_card_type_entries():
    # Multi-instance-of-one-type: a key outside PACKAGES_WITH_CARDS with an
    # explicit card_type is seeded as its own entry, carrying the card_type
    # through so the dashboard can resolve its adapter.
    body = dashboard_config(
        {
            "hemmingway-lane": {
                "card_type": "inference",
                "base_url": "https://hemmingway.example.com",
                "token": "t1",
            },
            "eclipse-lane": {
                "card_type": "inference",
                "base_url": "https://eclipse.example.com",
                "token": "t2",
                "enabled": False,
            },
        }
    )
    pkgs = body["packages"]
    assert pkgs["hemmingway-lane"]["card_type"] == "inference"
    assert pkgs["hemmingway-lane"]["enabled"] is True
    assert pkgs["eclipse-lane"]["card_type"] == "inference"
    assert pkgs["eclipse-lane"]["enabled"] is False
    # Fixed single-instance keys unaffected: no card_type key added to them.
    assert (
        "card_type"
        not in dashboard_config({"inference": {"base_url": "https://i.example.com"}})["packages"]["inference"]
    )


def test_dashboard_config_ignores_unknown_card_type():
    # A card_type that doesn't match a known adapter is not seeded at all
    # (fail-closed: no silent unrenderable card).
    body = dashboard_config({"mystery-lane": {"card_type": "nonexistent", "base_url": "https://x.example.com"}})
    assert "mystery-lane" not in body["packages"]


class _StaticAdapter:
    key = "static"
    label = "Static"

    def __init__(self, payload: dict):
        self._payload = payload

    def health(self, cfg: dict) -> Probe:
        return Probe(ok=True, data={"ok": True})

    def detail(self, cfg: dict) -> Probe:
        return Probe(ok=True, data=self._payload)


def _client(monkeypatch, cfg_packages):
    from toolkit.dashboard.api import build_fleet_api

    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "dash-token")
    adapter_map = {"static": _StaticAdapter({"headline": "v1"}), "finetune": finetune_adapter.ADAPTER}
    app = build_fleet_api(
        package_cfg={k: dict(v) for k, v in cfg_packages.items()},
        adapters=adapter_map,
    )
    return TestClient(app)


def test_stats_unauthorized_401(monkeypatch):
    client = _client(monkeypatch, {"static": {"base_url": "http://x", "token": "t"}})
    assert client.get("/_toolkit/api/stats").status_code == 401


def test_stats_sees_configured_enabled_only(monkeypatch):
    client = _client(
        monkeypatch,
        {
            "static": {"base_url": "http://x", "token": "t", "enabled": True},
            # second static adapter's section DISABLED -> card absent
        },
    )
    session = Session(token="dash-token")
    fake = client.get("/_toolkit/api/stats", cookies={"modal_toolkit_dashboard_session": session.value()})
    body = fake.json()
    assert fake.status_code == 200
    assert body["packages"]["static"]["health"]["ok"] is True
    assert body["packages"]["static"]["detail"]["data"]["headline"] == "v1"


def test_stats_renders_two_instances_of_one_card_type(monkeypatch):
    # The multi-instance-of-one-type request: two config entries share
    # card_type "static" (pointing at the same adapter) under distinct
    # keys, and both render as separate cards.
    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
    adapters_map = {"static": _StaticAdapter({"headline": "v1"})}
    cfg = {
        "lane-a": {"card_type": "static", "base_url": "http://x", "token": "ta"},
        "lane-b": {"card_type": "static", "base_url": "http://y", "token": "tb"},
    }
    client = TestClient(build_fleet_api(cfg, adapters_map))
    r = client.get("/_toolkit/api/stats", cookies={"modal_toolkit_dashboard_session": Session(token="t").value()})
    body = r.json()
    assert body["packages"]["lane-a"]["detail"]["data"]["headline"] == "v1"
    assert body["packages"]["lane-b"]["detail"]["data"]["headline"] == "v1"
    assert "lane-a" in body["order"]
    assert "lane-b" in body["order"]


def test_stats_card_type_entry_respects_its_own_enabled_flag(monkeypatch):
    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
    adapters_map = {"static": _StaticAdapter({"headline": "v1"})}
    cfg = {
        "lane-a": {"card_type": "static", "base_url": "http://x", "enabled": True},
        "lane-b": {"card_type": "static", "base_url": "http://y", "enabled": False},
    }
    client = TestClient(build_fleet_api(cfg, adapters_map))
    r = client.get("/_toolkit/api/stats", cookies={"modal_toolkit_dashboard_session": Session(token="t").value()})
    body = r.json()
    assert "lane-a" in body["packages"]
    assert "lane-b" not in body["packages"]


def test_stats_legacy_single_instance_config_unaffected():
    # No card_type anywhere: behavior is byte-for-byte the pre-existing
    # key == card-type contract.
    body = dashboard_config({"inference": {"base_url": "https://i.example.com", "token": "t"}})
    assert body["packages"]["inference"]["base_url"] == "https://i.example.com"
    assert "card_type" not in body["packages"]["inference"]


from toolkit.dashboard.api import build_fleet_api  # noqa: E402


def test_disabled_package_is_absent_not_error(monkeypatch):
    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
    adapters_map = {"a": _StaticAdapter({}), "b": _StaticAdapter({})}
    cfg = {"a": {"base_url": "http://x", "enabled": True}, "b": {"base_url": "http://y", "enabled": False}}
    client = TestClient(build_fleet_api(cfg, adapters_map))
    r = client.get("/_toolkit/api/stats", cookies={"modal_toolkit_dashboard_session": Session(token="t").value()})
    body = r.json()
    assert "b" not in body["packages"]
    assert "a" in body["packages"]


def test_finetune_card_renders_without_base_url(monkeypatch):
    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
    client = TestClient(build_fleet_api({"finetune": {}}, {"finetune": finetune_adapter.ADAPTER}))
    r = client.get("/_toolkit/api/stats", cookies={"modal_toolkit_dashboard_session": Session(token="t").value()})
    body = r.json()
    assert body["packages"]["finetune"]["health"]["data"]["status"] == "job-package"


def test_adapter_probes_never_raise_on_down_base(monkeypatch):
    # An unreachable base_url returns a failed Probe, not an exception.
    from toolkit.dashboard.adapters import embedding

    probe = embedding.ADAPTER.health({"base_url": "http://127.0.0.1:1", "token": ""})
    assert probe.ok is False
    assert probe.error is not None


def test_client_is_served_when_a_client_dir_is_present(tmp_path, monkeypatch):
    # The client bundle has to be reachable at /_toolkit and at its asset
    # path. The wheel only ships the toolkit/ package, so the bundle is
    # copied into the image; without it the dashboard rendered a 404.
    client = tmp_path / "client-dist"
    (client / "assets").mkdir(parents=True)
    (client / "index.html").write_text("<!doctype html><div id=app></div>")
    (client / "assets" / "dashboard.js").write_text("console.log('fleet')")

    monkeypatch.setenv("MODAL_TOOLKIT_DASHBOARD_TOKEN", "t")
    app = build_fleet_api({}, {"a": _StaticAdapter({})}, client_dir=client)
    test_client = TestClient(app)

    index = test_client.get("/_toolkit")
    assert index.status_code == 200
    assert "id=app" in index.text

    asset = test_client.get("/_toolkit/assets/dashboard.js")
    assert asset.status_code == 200
    assert "fleet" in asset.text


def test_client_is_404_when_no_client_dir_is_bundled():
    # No client bundled: the API says so plainly rather than serving an
    # empty page that looks like a broken dashboard.
    app = build_fleet_api({}, {"a": _StaticAdapter({})})
    response = TestClient(app).get("/_toolkit")
    assert response.status_code == 404
    assert "not bundled" in response.text
