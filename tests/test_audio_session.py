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


@pytest.fixture()
def _no_kicks():
    app._sip_kicked.clear()
    yield
    app._sip_kicked.clear()


class TestListenerDisconnect:
    """The board's X on a '<node> HenWen User' row hangs up that listener's
    call (admin+ only) and holds off the kiosk's automatic redial."""

    def _setup(self, monkeypatch, hung=1, found=None, error=None):
        calls = []
        detail = {"found": hung if found is None else found, "hung": hung, "error": error}
        monkeypatch.setattr(app, "_sip_session_callsigns", lambda: frozenset({"N8GMZ"}))
        monkeypatch.setattr(app, "_sip_hangup_endpoint_channels_detail",
                            lambda ep: calls.append(ep) or detail)
        return calls

    def _dc(self, client, remote="N8GMZ"):
        return client.post("/api/status/disconnect",
                           json={"local_node": "643930", "remote_node": remote})

    def test_admin_hangs_up_the_call(self, client, create_user, monkeypatch, _no_kicks):
        calls = self._setup(monkeypatch)
        create_user("owner1", role="owner")
        _login(client, "owner1")
        r = self._dc(client, "n8gmz")
        assert r.status_code == 200 and r.get_json() == {"ok": True, "hung_up": 1}
        assert calls == ["henwen-u-n8gmz"]
        assert app._sip_kick_remaining("N8GMZ") > 0

    def test_user_role_is_refused(self, client, create_user, monkeypatch, _no_kicks):
        calls = self._setup(monkeypatch)
        create_user("owner1", role="owner")
        create_user("kiosk1", role="user")
        _login(client, "kiosk1")
        r = self._dc(client)
        assert r.status_code == 403
        assert calls == [] and app._sip_kick_remaining("N8GMZ") == 0

    def test_no_live_call_is_404_and_no_hold(self, client, create_user, monkeypatch, _no_kicks):
        self._setup(monkeypatch, hung=0)
        create_user("owner1", role="owner")
        _login(client, "owner1")
        assert self._dc(client).status_code == 404
        assert app._sip_kick_remaining("N8GMZ") == 0

    def test_partial_hangup_keeps_the_hold(self, client, create_user, monkeypatch, _no_kicks):
        """PR #10 review: a call may have ended even though the hangup
        reported an error -- that listener must not be handed a credential
        back by the 3s redial."""
        self._setup(monkeypatch, hung=1, found=2, error="hangup of x failed")
        create_user("owner1", role="owner")
        _login(client, "owner1")
        r = self._dc(client)
        assert r.status_code == 502 and r.get_json()["hung_up"] == 1
        assert "hangup of x failed" not in r.get_data(as_text=True)   # no internals to the client
        assert app._sip_kick_remaining("N8GMZ") > 0

    def test_listing_failure_keeps_the_hold(self, client, create_user, monkeypatch, _no_kicks):
        self._setup(monkeypatch, hung=0, found=0, error="could not list channels")
        create_user("owner1", role="owner")
        _login(client, "owner1")
        assert self._dc(client).status_code == 502
        assert app._sip_kick_remaining("N8GMZ") > 0

    def test_unprovisioned_callsign_is_still_invalid(self, client, create_user, monkeypatch, _no_kicks):
        calls = self._setup(monkeypatch)
        create_user("owner1", role="owner")
        _login(client, "owner1")
        assert self._dc(client, "NT0Y").status_code == 400     # an IAX client, not a listener
        assert self._dc(client, "0HenWen-Web").status_code == 400
        assert calls == []

    def test_kicked_listener_is_told_to_stop(self, client, create_user, fresh_db, _no_kicks):
        create_user("owner1", role="owner")
        _user(fresh_db, "bob", "bobs-password-1", callsign="N8GMZ")
        app._sip_kick_hold("N8GMZ", "owner1")
        _login(client, "bob", "bobs-password-1")
        b = client.get("/api/audio/session").get_json()
        assert b["kicked"] is True and b["reason"] == "kicked"
        assert b["sip"] is None and 0 < b["retry_after"] <= app.SIP_KICK_HOLD_SEC
        assert "AbCdEf" not in client.get("/api/audio/session").get_data(as_text=True)

    def test_own_disconnect_allows_a_deliberate_listen(self, client, fresh_db, _no_kicks):
        """The owner hung up their own call from another browser: a Listen
        press (manual=1) goes through, an automatic redial is still stopped,
        and the hold survives for the browser that was hung up."""
        _user(fresh_db, "Levi", "levis-password-1", callsign="N8GMZ", role="owner")
        app._sip_kick_hold("N8GMZ", "levi")           # usernames are NOCASE
        _login(client, "Levi", "levis-password-1")
        b = client.get("/api/audio/session?manual=1").get_json()
        assert not b.get("kicked") and b.get("reason") != "kicked"
        b = client.get("/api/audio/session").get_json()
        assert b["kicked"] is True and b["by_self"] is True
        assert app._sip_kick_remaining("N8GMZ") > 0

    def test_manual_listen_does_not_undo_someone_elses_disconnect(self, client, create_user,
                                                                   fresh_db, _no_kicks):
        create_user("owner1", role="owner")
        _user(fresh_db, "bob", "bobs-password-1", callsign="N8GMZ")
        app._sip_kick_hold("N8GMZ", "owner1")
        _login(client, "bob", "bobs-password-1")
        b = client.get("/api/audio/session?manual=1").get_json()
        assert b["kicked"] is True and b["by_self"] is False and b["sip"] is None

    def test_hold_expires(self, _no_kicks):
        app._sip_kick_hold("N8GMZ", "owner1", now=1000.0)
        assert app._sip_kick_remaining("N8GMZ", now=1000.0 + app.SIP_KICK_HOLD_SEC - 1) == 1
        assert app._sip_kick_remaining("N8GMZ", now=1000.0 + app.SIP_KICK_HOLD_SEC) == 0
        assert "N8GMZ" not in app._sip_kicked
