"""HenWen 2.0 Phase 3: /api/audio/session decides, per logged-in session,
whether Listen is one native SIP call (this account's own identity) or the
1.x stream. The credential rules are the ones that matter: who gets `sip`
and who never does.
"""
import pytest

import app


def _login(client, username, password="password12345"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302, f"login as {username} failed: {r.status_code}"
    return r


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    app.limiter.reset()


@pytest.fixture(autouse=True)
def _wired(tmp_path, monkeypatch):
    """Generated files present and includes wired, so a callsign'd account
    has a *usable* identity; tests for the unwired state override."""
    conf = tmp_path / "henwen-sip-users.conf"
    conf.write_text("; generated\n")
    monkeypatch.setattr(app, "SIP_USERS_CONF", str(conf))
    monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "dialplan.conf"))
    rpt = tmp_path / "rpt.conf"
    rpt.write_text("[643930]\nrxchannel = SimpleUSB/usb\ntotime = 120000\n")
    monkeypatch.setattr(app, "RPT_CONF_PATH", str(rpt))
    monkeypatch.setattr(app, "sip_include_status", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(app, "_sip_reload_asterisk", lambda: (True, "ok"))
    monkeypatch.setattr(app, "_sip_hangup_endpoint_channels", lambda ep: 0)


def _user(fresh_db, username, password, callsign=None, listen_only=0, role="user"):
    from werkzeug.security import generate_password_hash
    fresh_db.execute(
        "INSERT INTO users (username, password_hash, role, listen_only, callsign, sip_secret) "
        "VALUES (?,?,?,?,?,?)",
        (username, generate_password_hash(password), role, listen_only, callsign,
         "AbCdEf0123456789-_xyzQRS" if callsign else None))
    fresh_db.commit()


class TestAudioSession:
    def test_requires_login(self, client, create_user):
        create_user("owner1", role="owner")
        assert client.get("/api/audio/session").status_code == 401

    def test_identity_account_gets_one_call(self, client, create_user, fresh_db):
        create_user("owner1", role="owner")
        _user(fresh_db, "bob", "bobs-password-1", callsign="N8GMZ")
        _login(client, "bob", "bobs-password-1")
        r = client.get("/api/audio/session")
        assert r.status_code == 200
        b = r.get_json()
        assert b["node"] == "643930"
        assert b["identity"] == "user" and b["can_tx"] is True
        assert b["sip"]["username"] == "henwen-u-n8gmz"
        assert b["sip"]["auth_user"] == "N8GMZ"
        assert b["sip"]["password"] == "AbCdEf0123456789-_xyzQRS"
        assert b["sip"]["dial"] == "643930"          # own context: bare node, not 2<node>
        assert b["sip"]["link_name"] == "N8GMZ"
        assert b["sip"]["tot_sec"] == 120
        assert b["sip"]["ws_path"] == app.TX_WS_PATH
        assert r.headers["Cache-Control"] == "no-store"

    def test_no_identity_means_legacy(self, client, create_user):
        create_user("owner1", role="owner")
        _login(client, "owner1")
        b = client.get("/api/audio/session").get_json()
        assert b["sip"] is None and b["identity"] is None and b["can_tx"] is False
        assert b["reason"] == "no_identity"
        assert b["legacy_path"] in app.RX_AUDIO_PATHS

    def test_listen_only_never_gets_a_credential(self, client, create_user, fresh_db):
        """D1-a: an issued credential can key. Listen-only stays on the
        stream until Phase 5 enforces it inside the call."""
        create_user("owner1", role="owner")
        _user(fresh_db, "lo", "listeners-password1", callsign="N0CALL", listen_only=1)
        _login(client, "lo", "listeners-password1")
        b = client.get("/api/audio/session").get_json()
        assert b["sip"] is None and b["can_tx"] is False and b["reason"] == "listen_only"
        assert "AbCdEf" not in client.get("/api/audio/session").get_data(as_text=True)

    def test_unwired_includes_mean_legacy(self, client, create_user, fresh_db, monkeypatch):
        monkeypatch.setattr(app, "sip_include_status", lambda *a, **k: {"ready": False})
        create_user("owner1", role="owner")
        _user(fresh_db, "bob", "bobs-password-1", callsign="N8GMZ")
        _login(client, "bob", "bobs-password-1")
        b = client.get("/api/audio/session").get_json()
        assert b["sip"] is None and b["reason"] == "no_identity"

    def test_lockout_withholds_the_call_from_non_owners(self, client, create_user, fresh_db, monkeypatch):
        monkeypatch.setattr(app, "is_any_node_locked", lambda: True)
        create_user("owner1", role="owner")
        _user(fresh_db, "bob", "bobs-password-1", callsign="N8GMZ")
        _login(client, "bob", "bobs-password-1")
        assert client.get("/api/audio/session").get_json()["reason"] == "locked"

    def test_owner_with_identity_during_lockout_still_gets_it(self, client, create_user, fresh_db, monkeypatch):
        monkeypatch.setattr(app, "is_any_node_locked", lambda: True)
        _user(fresh_db, "own", "owners-password-1", callsign="N8GMZ", role="owner")
        _login(client, "own", "owners-password-1")
        assert client.get("/api/audio/session").get_json()["identity"] == "user"

    def test_shared_tx_credential_is_never_offered_here(self, client, create_user, tmp_path, monkeypatch):
        sec = tmp_path / "henwen-tx.secret"
        sec.write_text("shared-s3kret\n")
        monkeypatch.setattr(app, "TX_SECRET_PATH", str(sec))
        create_user("owner1", role="owner")
        _login(client, "owner1")
        body = client.get("/api/audio/session").get_data(as_text=True)
        assert "shared-s3kret" not in body and "henwen-tx" not in body


class TestKioskPage:
    def test_board_carries_the_one_call_path(self, client, create_user):
        create_user("owner1", role="owner")
        html = client.get("/").get_data(as_text=True)
        for needle in ('id="mute-btn"', "_doStartListenSIP", "/api/audio/session",
                       "authorization_user", "_sipFallback", "_startListenLegacy"):
            assert needle in html, needle
