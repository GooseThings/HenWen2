"""Phase W2 (docs/ASL-WEB-UI-PLAN.md): where the ASL Web UI is reachable,
HenWen stops writing the parts of rpt.conf it covers."""
import pytest

import app

CONF = """[general]

[643930]
rxchannel = dahdi/pseudo
duplex = 0

[functions]
1 = ilink,1

[macro]
1 = *32000

[schedule]
1 = 00 00 * * *
"""


class TestBlocks:
    def test_raw_and_restore_are_handed_over(self):
        assert app._asl_handover_blocks("api_save", {"raw_content": "x"}, CONF)
        assert app._asl_handover_blocks("api_backup_restore", None, CONF)

    def test_node_and_general_stanzas_are_handed_over(self):
        for section in ("643930", "general", "node-main"):
            assert app._asl_handover_blocks("api_save", {"section": section, "changes": {}}, CONF)

    def test_macros_and_schedules_stay(self):
        for section in ("macro", "schedule"):
            assert not app._asl_handover_blocks("api_save", {"section": section, "changes": {}}, CONF)

    def test_other_endpoints_untouched(self):
        assert not app._asl_handover_blocks("api_node_access", {}, CONF)


@pytest.fixture()
def owner(client, create_user, tmp_path, monkeypatch):
    conf = tmp_path / "rpt.conf"
    conf.write_text(CONF)
    monkeypatch.setattr(app, "RPT_CONF_PATH", str(conf))
    monkeypatch.setattr(app, "BACKUP_DIR", str(tmp_path / "bak"))
    app._conf_file_cache.clear()
    row = create_user("own", role="owner")
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="own", role="owner", user_id=row["id"],
                    password_epoch=row["password_epoch"], sid="t-own",
                    idle_timeout=app.SESSION_IDLE_TIMEOUT)
    return client, conf


VIA_APACHE = {"X-Forwarded-Host": "henwen.example.com", "X-Forwarded-Server": "henwen.example.com"}


def _handover(monkeypatch, on):
    monkeypatch.setattr(app, "asl_web_ui_status",
                        lambda: {"handover": on, "url": "/asl-web-ui/" if on else None})


def test_gate_refuses_a_handed_over_save(owner, monkeypatch):
    client, conf = owner
    _handover(monkeypatch, True)
    r = client.post("/api/save", json={"section": "643930",
                                       "changes": {"duplex": {"value": "1", "enabled": True}}},
                    headers=VIA_APACHE)
    assert r.status_code == 409
    body = r.get_json()
    assert body["handed_over"] is True and body["url"] == "/asl-web-ui/"
    assert "duplex = 0" in conf.read_text()


def test_gate_lets_macro_saves_through(owner, monkeypatch):
    client, conf = owner
    _handover(monkeypatch, True)
    r = client.post("/api/save", json={"section": "macro",
                                       "changes": {"2": {"value": "*32001", "enabled": True}}},
                    headers=VIA_APACHE)
    assert r.status_code == 200 and "*32001" in conf.read_text()


def test_no_handover_no_gate(owner, monkeypatch):
    client, conf = owner
    _handover(monkeypatch, False)
    r = client.post("/api/save", json={"raw_content": CONF + "; x\n"})
    assert r.status_code == 200


def test_handover_needs_reachable_and_not_disabled(tmp_path, monkeypatch):
    www = tmp_path / "www"; www.mkdir(); (www / "index.html").write_text("")
    glob = tmp_path / "g.conf"; glob.write_text("")
    sites = tmp_path / "sites"; sites.mkdir()
    vhost = sites / "henwen.conf"
    vhost.write_text(f"<VirtualHost *:443>\n    ProxyPass / http://127.0.0.1:{app.PORT}/\n</VirtualHost>\n")
    monkeypatch.setattr(app, "HENWEN_APACHE_SITES_DIR", str(sites))
    monkeypatch.setattr(app, "HENWEN_APACHE_VHOST_CANDIDATES", ())
    monkeypatch.setattr(app, "ASL_WEB_UI_WWW", str(www))
    monkeypatch.setattr(app, "ASL_WEB_UI_GLOBAL_CONF", str(glob))
    monkeypatch.setattr(app, "ASL_WEB_UI_SOCKET", str(tmp_path / "web.sock"))
    (tmp_path / "web.sock").write_text("")
    monkeypatch.setattr(app, "ASL_WEB_UI_HANDOVER", True)
    app.asl_web_ui_status.cache_clear()
    # Review of PR #18: the package's default state -- global conf on, API
    # up, HenWen's vhost not yet scoped -- sends /asl-web-ui/ to Flask, so
    # nothing is handed over until apply.sh has run.
    assert app.asl_web_ui_status()["handover"] is False
    vhost.write_text(vhost.read_text().replace("    ProxyPass /", "    # HenWen: ASL Web UI.\n    ProxyPass /"))
    (tmp_path / "web.sock").unlink()
    monkeypatch.setattr(app, "ASL_WEB_UI_HANDOVER", True)
    app.asl_web_ui_status.cache_clear()
    # PR #18 review: installed and reachable but its API socket is down, so
    # it can't save either -- HenWen keeps editing.
    assert app.asl_web_ui_status()["handover"] is False
    (tmp_path / "web.sock").write_text("")
    app.asl_web_ui_status.cache_clear()
    assert app.asl_web_ui_status()["handover"] is True
    monkeypatch.setattr(app, "ASL_WEB_UI_HANDOVER", False)
    app.asl_web_ui_status.cache_clear()
    assert app.asl_web_ui_status()["handover"] is False
    app.asl_web_ui_status.cache_clear()


def test_direct_visit_keeps_editing(owner, monkeypatch):
    """PR #18 review: on HenWen's own port /asl-web-ui/ doesn't exist, so a
    request that didn't come through Apache is never handed over."""
    client, conf = owner
    _handover(monkeypatch, True)
    r = client.post("/api/save", json={"section": "643930",
                                       "changes": {"duplex": {"value": "1", "enabled": True}}})
    assert r.status_code == 200 and "duplex = 1" in conf.read_text()
    st = client.get("/api/asl-web-ui/status").get_json()
    assert st["handover"] is False and st["url"] is None and st["direct_visit"] is True
    st = client.get("/api/asl-web-ui/status", headers=VIA_APACHE).get_json()
    assert st["handover"] is True and st["url"] == "/asl-web-ui/"


def test_read_only_pages_disable_controls():
    """PR #18 review: pointer-events alone left keyboard edits possible;
    the controls are disabled, including whatever the editors re-render."""
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "henwen-manager.html")).read()
    assert "pointer-events: none" not in src.split("W2 handover", 1)[1].split("}", 1)[0]
    assert "else el.disabled = true;" in src
    assert "new MutationObserver(function() { _aslDisableControls(sec); })" in src
