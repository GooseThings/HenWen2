"""Per-user SIP identity, second slice (HenWen 2.0 Phase 2): the assignment
route, the provisioning trigger, revocation, and what /api/tx/config hands
a session. Everything that touches Asterisk (the reload, the hangup) is
stubbed and recorded; the generated files go to a temp dir.
"""
import os
import subprocess

import pytest

import app


def _login(client, username, password="password12345"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302, f"login as {username} failed: {r.status_code}"
    return r


def _logout(client):
    return client.post("/logout")


# The autouse fixture below stubs these on the app module for every test;
# the tests that exercise the real implementations call the originals.
_REAL_RELOAD = app._sip_reload_asterisk
_REAL_HANGUP = app._sip_hangup_endpoint_channels


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """/login carries a real rate limit whose in-memory storage outlives
    conftest's RATELIMIT_ENABLED=False (the limiter is built at import), so
    a file with this many logins trips it mid-run -- same fixture as
    test_listen_only.py."""
    app.limiter.reset()


@pytest.fixture()
def owner(client, create_user):
    create_user("owner1", role="owner")
    _login(client, "owner1")
    return client


@pytest.fixture(autouse=True)
def _isolated_sip(tmp_path, monkeypatch):
    """Generated files in a temp dir, a node in rpt.conf, and Asterisk
    stubbed: every test here gets the same quiet world."""
    monkeypatch.setattr(app, "SIP_USERS_CONF", str(tmp_path / "henwen-sip-users.conf"))
    monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "henwen-sip-users-dialplan.conf"))
    rpt = tmp_path / "rpt.conf"
    rpt.write_text("[643930]\nrxchannel = SimpleUSB/usb\n")
    monkeypatch.setattr(app, "RPT_CONF_PATH", str(rpt))
    calls = {"reload": 0, "hangup": []}
    monkeypatch.setattr(app, "_sip_reload_asterisk", lambda: (calls.__setitem__("reload", calls["reload"] + 1) or (True, "ok")))
    monkeypatch.setattr(app, "_sip_hangup_endpoint_channels", lambda ep: calls["hangup"].append(ep) or 0)
    # Includes "wired" by default; tests that want the unwired state override.
    monkeypatch.setattr(app, "sip_include_status", lambda *a, **k: {"ready": True,
                        "pjsip": {"included": True, "readable": True, "path": "p"},
                        "dialplan": {"included": True, "readable": True, "path": "e"}})
    return calls


def _row(fresh_db, username):
    return fresh_db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


@pytest.fixture
def _session_callsigns(monkeypatch):
    """Pretend N8GMZ is a provisioned account, without a DB round trip."""
    monkeypatch.setattr(app, "_sip_session_callsigns", lambda: frozenset({"N8GMZ"}))


class TestSessionLink:
    def test_session_links_are_recognised(self, _session_callsigns):
        assert app._sip_session_link("0HenWen-Web")
        assert app._sip_session_link("N8GMZ")
        assert app._sip_session_link("n8gmz")
        # A call placed before the rename keeps its old name until it redials.
        assert app._sip_session_link("0N8GMZ")

    def test_nodes_and_iax_clients_are_not(self, _session_callsigns):
        # NT0Y is a callsign too -- an IAX client, not a HenWen account.
        for peer in ("643930", "27664", "NT0Y", "", None, "0"):
            assert not app._sip_session_link(peer)

    def test_provisioning_a_callsign_makes_it_a_session_link(self, owner, fresh_db, _isolated_sip):
        app._sip_session_callsigns.cache_clear()
        assert not app._sip_session_link("N8GMZ")
        r = owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                           "role": "user", "callsign": "N8GMZ"})
        assert r.status_code == 200, r.get_json()
        assert app._sip_session_link("N8GMZ")


class TestCreateWithCallsign:
    def test_callsign_is_stored_normalized_with_a_secret(self, owner, fresh_db, _isolated_sip):
        r = owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                           "role": "user", "callsign": " n8gmz "})
        assert r.status_code == 200, r.get_json()
        row = _row(fresh_db, "bob")
        assert row["callsign"] == "N8GMZ"
        assert row["sip_secret"] and len(row["sip_secret"]) >= 30
        # Provisioned: both files written, reload fired.
        assert os.path.exists(app.SIP_USERS_CONF)
        assert "henwen-u-n8gmz" in open(app.SIP_USERS_CONF).read()
        assert "Rpt(643930,P,N8GMZ)" in open(app.SIP_USERS_DIALPLAN).read()
        assert _isolated_sip["reload"] == 1

    def test_no_callsign_means_no_secret_and_no_provisioning(self, owner, fresh_db, _isolated_sip):
        r = owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1", "role": "user"})
        assert r.status_code == 200
        row = _row(fresh_db, "bob")
        assert row["callsign"] is None and row["sip_secret"] is None
        assert not os.path.exists(app.SIP_USERS_CONF)

    def test_invalid_callsign_is_a_400(self, owner):
        r = owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                           "role": "user", "callsign": "ADMIN"})
        assert r.status_code == 400 and "callsign" in r.get_json()["error"]

    def test_duplicate_callsign_is_a_409_case_insensitively(self, owner):
        assert owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                              "role": "user", "callsign": "N8GMZ"}).status_code == 200
        r = owner.post("/api/users", json={"username": "carol", "password": "carols-password-1",
                                           "role": "user", "callsign": "n8gmz"})
        assert r.status_code == 409 and "N8GMZ" in r.get_json()["error"]

    def test_list_shows_callsign_but_never_the_secret(self, owner):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": "N8GMZ"})
        r = owner.get("/api/users")
        users = {u["username"]: u for u in r.get_json()["users"]}
        assert users["bob"]["callsign"] == "N8GMZ"
        assert users["bob"]["sip_provisioned"] is True
        assert users["owner1"]["callsign"] == "" and users["owner1"]["sip_provisioned"] is False
        assert "sip_secret" not in r.get_data(as_text=True)


class TestUpdate:
    def _bob(self, owner, callsign="N8GMZ"):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": callsign})
        return owner.get("/api/users").get_json()["users"]
    def _bob_id(self, owner, **kw):
        return [u for u in self._bob(owner, **kw) if u["username"] == "bob"][0]["id"]

    def test_assigning_a_callsign_later_generates_a_secret(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner, callsign="")
        assert _row(fresh_db, "bob")["sip_secret"] is None
        r = owner.put(f"/api/users/{uid}", json={"callsign": "n8gmz"})
        assert r.status_code == 200
        row = _row(fresh_db, "bob")
        assert row["callsign"] == "N8GMZ" and row["sip_secret"]
        assert _isolated_sip["reload"] == 1 and _isolated_sip["hangup"] == []

    def test_changing_the_callsign_rotates_the_secret_and_ends_the_old_endpoints_calls(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner)
        old = _row(fresh_db, "bob")["sip_secret"]
        assert owner.put(f"/api/users/{uid}", json={"callsign": "N8GMX"}).status_code == 200
        row = _row(fresh_db, "bob")
        assert row["callsign"] == "N8GMX" and row["sip_secret"] != old
        assert _isolated_sip["hangup"] == ["henwen-u-n8gmz"]
        assert "henwen-u-n8gmz]" not in open(app.SIP_USERS_CONF).read()
        assert "[henwen-u-n8gmx]" in open(app.SIP_USERS_CONF).read()

    def test_same_callsign_resent_changes_nothing(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner)
        old = _row(fresh_db, "bob")["sip_secret"]
        assert owner.put(f"/api/users/{uid}", json={"callsign": "n8gmz", "role": "user"}).status_code == 200
        assert _row(fresh_db, "bob")["sip_secret"] == old
        assert _isolated_sip["reload"] == 1   # only the create's

    def test_clearing_the_callsign_revokes(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner)
        assert owner.put(f"/api/users/{uid}", json={"callsign": ""}).status_code == 200
        row = _row(fresh_db, "bob")
        assert row["callsign"] is None and row["sip_secret"] is None
        assert _isolated_sip["hangup"] == ["henwen-u-n8gmz"]
        assert "henwen-u-" not in open(app.SIP_USERS_CONF).read()

    def test_password_change_rotates_the_secret(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner)
        old = _row(fresh_db, "bob")["sip_secret"]
        assert owner.put(f"/api/users/{uid}", json={"password": "a-brand-new-password-2"}).status_code == 200
        assert _row(fresh_db, "bob")["sip_secret"] != old
        assert _isolated_sip["hangup"] == ["henwen-u-n8gmz"]

    def test_password_change_without_a_callsign_touches_nothing(self, owner, fresh_db, _isolated_sip):
        uid = self._bob_id(owner, callsign="")
        assert owner.put(f"/api/users/{uid}", json={"password": "a-brand-new-password-2"}).status_code == 200
        assert _row(fresh_db, "bob")["sip_secret"] is None
        assert _isolated_sip["reload"] == 0 and _isolated_sip["hangup"] == []

    def test_duplicate_on_update_is_a_409(self, owner):
        uid = self._bob_id(owner)
        owner.post("/api/users", json={"username": "carol", "password": "carols-password-1",
                                       "role": "user", "callsign": "N8GMX"})
        assert owner.put(f"/api/users/{uid}", json={"callsign": "n8gmx"}).status_code == 409

    def test_self_service_password_change_rotates_too(self, client, fresh_db, owner, _isolated_sip):
        uid = self._bob_id(owner)
        _logout(client)
        _login(client, "bob", "bobs-password-1")
        old = _row(fresh_db, "bob")["sip_secret"]
        r = client.post("/api/auth/change-password", json={"current_password": "bobs-password-1",
                                                           "new_password": "a-brand-new-password-2"})
        assert r.status_code == 200, r.get_json()
        assert _row(fresh_db, "bob")["sip_secret"] != old
        assert "henwen-u-n8gmz" in _isolated_sip["hangup"]


class TestDelete:
    def test_delete_removes_the_endpoint_and_ends_its_calls(self, owner, _isolated_sip):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": "N8GMZ"})
        uid = [u for u in owner.get("/api/users").get_json()["users"] if u["username"] == "bob"][0]["id"]
        assert owner.delete(f"/api/users/{uid}").status_code == 200
        assert "henwen-u-" not in open(app.SIP_USERS_CONF).read()
        assert _isolated_sip["hangup"] == ["henwen-u-n8gmz"]
        assert _isolated_sip["reload"] == 2


class TestProvisioning:
    def test_unwired_includes_write_but_do_not_reload(self, owner, fresh_db, _isolated_sip, monkeypatch):
        monkeypatch.setattr(app, "sip_include_status", lambda *a, **k: {"ready": False,
                            "pjsip": {"included": False}, "dialplan": {"included": False}})
        r = owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                           "role": "user", "callsign": "N8GMZ"})
        assert r.status_code == 200
        body = r.get_json()
        assert os.path.exists(app.SIP_USERS_CONF)
        assert _isolated_sip["reload"] == 0
        assert body["sip"]["included"] is False
        assert "apply.sh" in body["warning"]

    def test_unchanged_files_do_not_reload_unless_forced(self, fresh_db, _isolated_sip):
        fresh_db.execute("INSERT INTO users (username, password_hash, role, callsign, sip_secret) "
                         "VALUES ('bob','x','user','N8GMZ','AbCdEf0123456789-_xyzQRS')")
        fresh_db.commit()
        assert app.sip_provision_accounts("first")["reloaded"] is True
        assert app.sip_provision_accounts("again")["reloaded"] is False
        assert app.sip_provision_accounts("forced", force_reload=True)["reloaded"] is True
        assert _isolated_sip["reload"] == 2

    def test_no_node_writes_nothing(self, fresh_db, _isolated_sip, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "RPT_CONF_PATH", str(tmp_path / "missing.conf"))
        r = app.sip_provision_accounts("x")
        assert r["ok"] is False and "node" in r["error"]
        assert not os.path.exists(app.SIP_USERS_CONF)

    def test_reload_failure_is_reported_not_raised(self, fresh_db, _isolated_sip, monkeypatch):
        monkeypatch.setattr(app, "_sip_reload_asterisk", lambda: (False, "AMI down"))
        fresh_db.execute("INSERT INTO users (username, password_hash, role, callsign, sip_secret) "
                         "VALUES ('bob','x','user','N8GMZ','AbCdEf0123456789-_xyzQRS')")
        fresh_db.commit()
        r = app.sip_provision_accounts("x")
        assert r["ok"] is False and r["error"] == "AMI down"
        assert os.path.exists(app.SIP_USERS_CONF)   # the files still landed


class TestReloadCommand:
    def test_unknown_command_in_output_is_a_failure(self, monkeypatch):
        class Ami:
            def command(self, c, **kw):
                return ["No such command 'pjsip reload' (type 'core show help pjsip reload' for other possible commands)"]
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        ok, detail = _REAL_RELOAD()
        assert ok is False and "does not know" in detail

    def test_quiet_output_is_success(self, monkeypatch):
        seen = []
        class Ami:
            def command(self, c, **kw):
                seen.append(c); return []
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        ok, _ = _REAL_RELOAD()
        assert ok is True
        assert seen == ["module reload res_pjsip.so", "dialplan reload"]


class TestHangup:
    def test_only_that_endpoints_channels_are_hung_up(self, monkeypatch):
        hung = []
        row = lambda name: f"{name}!henwen-u-n8gmz!643930!1!Up!Rpt!643930,P,N8GMZ!N8GMZ!!!3!42!!1700000000.1"
        class Ami:
            timeout = 2
            def command(self, c, **kw):
                return [row("PJSIP/henwen-u-n8gmz-00000012"), row("PJSIP/henwen-u-n8gmx-00000013"),
                        row("PJSIP/henwen-tx-00000014")]
            def _send_action(self, a): hung.append(a["Channel"])
            def _recv_until(self, *a, **k): return "Response: Success\r\n\r\n"
            def _parse_packet(self, raw): return {"Response": "Success"}
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        assert _REAL_HANGUP("henwen-u-n8gmz") == 1
        assert hung == ["PJSIP/henwen-u-n8gmz-00000012"]

    def test_ami_failure_is_swallowed(self, monkeypatch):
        def boom(fn): raise RuntimeError("AMI down")
        monkeypatch.setattr(app, "ami_send_command", boom)
        assert _REAL_HANGUP("henwen-u-n8gmz") == 0


class TestTxConfig:
    def _secret(self, tmp_path, monkeypatch, present=True):
        p = tmp_path / "henwen-tx.secret"
        if present:
            p.write_text("shared-s3kret\n")
        monkeypatch.setattr(app, "TX_SECRET_PATH", str(p))

    def _bob_with_identity(self, owner, client):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": "N8GMZ"})
        _logout(client)
        _login(client, "bob", "bobs-password-1")

    def test_provisioned_account_gets_its_own_credential(self, owner, client, fresh_db, tmp_path, monkeypatch):
        self._secret(tmp_path, monkeypatch)
        self._bob_with_identity(owner, client)
        r = client.get("/api/tx/config")
        assert r.status_code == 200, r.get_json()
        b = r.get_json()
        assert b["identity"] == "user"
        assert b["username"] == "henwen-u-n8gmz"       # SIP user = endpoint name
        assert b["auth_user"] == "N8GMZ"               # auth username = callsign
        assert b["password"] == _row(fresh_db, "bob")["sip_secret"]
        assert b["dial"] == "643930"                   # own context: bare node
        assert b["link_name"] == "N8GMZ"
        assert r.headers["Cache-Control"] == "no-store"

    def test_probe_says_which_identity(self, owner, client, tmp_path, monkeypatch):
        self._secret(tmp_path, monkeypatch)
        self._bob_with_identity(owner, client)
        assert client.get("/api/tx/config?probe=1").get_json() == {"enabled": True, "identity": "user"}

    def test_unprovisioned_account_gets_the_shared_credential(self, owner, tmp_path, monkeypatch):
        self._secret(tmp_path, monkeypatch)
        b = owner.get("/api/tx/config").get_json()
        assert b["identity"] == "shared"
        assert b["username"] == app.TX_SIP_USER and b["auth_user"] == app.TX_SIP_USER
        assert b["password"] == "shared-s3kret" and b["dial"] == "2643930"

    def test_unwired_includes_fall_back_to_shared(self, owner, client, tmp_path, monkeypatch):
        self._secret(tmp_path, monkeypatch)
        monkeypatch.setattr(app, "sip_include_status", lambda *a, **k: {"ready": False})
        self._bob_with_identity(owner, client)
        assert client.get("/api/tx/config").get_json()["identity"] == "shared"

    def test_own_identity_works_without_the_shared_secret(self, owner, client, tmp_path, monkeypatch):
        self._secret(tmp_path, monkeypatch, present=False)
        self._bob_with_identity(owner, client)
        assert client.get("/api/tx/config").get_json()["identity"] == "user"
        _logout(client)
        _login(client, "owner1")
        assert client.get("/api/tx/config").status_code == 404

    def test_listen_only_still_gets_nothing(self, owner, client, fresh_db, tmp_path, monkeypatch):
        """Phase 2 keeps 1.x's rule: under D1-a any issued credential can key,
        and the frontend that hides PTT for a listen-only call is Phase 3."""
        self._secret(tmp_path, monkeypatch)
        owner.post("/api/users", json={"username": "lo", "password": "listeners-password1",
                                       "role": "user", "callsign": "N0CALL", "listen_only": True})
        _logout(client)
        _login(client, "lo", "listeners-password1")
        assert client.get("/api/tx/config").status_code == 404


class TestApplyRoute:
    def test_non_owner_is_refused(self, client, create_user):
        create_user("owner1", role="owner")   # out of first-run setup mode
        create_user("adm", role="admin")
        _login(client, "adm")
        assert client.get("/api/sip-users/status").status_code == 403
        assert client.post("/api/sip-users/apply").status_code == 403

    def test_status_shape(self, owner):
        d = owner.get("/api/sip-users/status").get_json()
        assert set(d) >= {"installed", "included", "accounts", "files_written", "tx_applied"}
        assert d["included"] is True and d["accounts"] == 0

    def test_apply_runs_exact_sudoers_command_then_provisions(self, owner, monkeypatch, _isolated_sip):
        seen = {}
        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "== Done\n", "")
        monkeypatch.setattr(app.subprocess, "run", fake_run)
        monkeypatch.setattr(app, "SIP_USERS_APPLY_SCRIPT_PATH", "/opt/HenWen/sip-users/apply.sh")
        monkeypatch.setattr(app.os.path, "exists", lambda p: True)
        r = owner.post("/api/sip-users/apply")
        assert r.status_code == 200, r.get_json()
        # Must match provision-sudoers.sh's rule argument-for-argument.
        assert seen["cmd"] == [app.SUDO_PATH, "-n", "/opt/HenWen/sip-users/apply.sh"]
        assert r.get_json()["provision"]["reloaded"] is True   # force_reload

    def test_sudo_failure_carries_a_hint(self, owner, monkeypatch):
        monkeypatch.setattr(app.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, "", "sudo: a password is required"))
        monkeypatch.setattr(app.os.path, "exists", lambda p: True)
        r = owner.post("/api/sip-users/apply")
        assert r.status_code == 500 and "sudoers" in r.get_json()["hint"]

    def test_sudoers_rule_matches_the_route(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(here, "provision-sudoers.sh")).read()
        assert "${INSTALL_DIR}/sip-users/apply.sh" in text


class TestConnectorExclusion:
    def test_disconnect_all_skips_session_links(self, monkeypatch, _session_callsigns):
        sent = []
        class Ami:
            def rpt_cmd(self, node, cmd):
                sent.append(cmd); return {"success": True}
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        monkeypatch.setitem(app._ami_cache, "643930",
                            {"connected": ["27664", "N8GMZ", "0HenWen-Web", "2000"]})
        monkeypatch.setattr(app.time, "sleep", lambda s: None)
        app._connector_do_connect("643930", "55553", disconnect_first=True, skip_permanent=False)
        assert sent == ["ilink 11 27664", "ilink 11 2000", "ilink 3 55553"]

    def test_preserve_permanent_variant_skips_them_too(self, monkeypatch, _session_callsigns):
        sent = []
        class Ami:
            def rpt_cmd(self, node, cmd):
                sent.append(cmd); return {"success": True}
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        monkeypatch.setitem(app._ami_cache, "643930", {"connected": ["27664", "N8GMZ"]})
        monkeypatch.setattr(app.time, "sleep", lambda s: None)
        app._connector_do_connect("643930", "55553", disconnect_first=True, skip_permanent=True)
        assert sent == ["ilink 11 27664", "ilink 3 55553"]


class TestListenOnlyFlip:
    def test_turning_listen_only_on_revokes_the_live_credential(self, owner, fresh_db, _isolated_sip):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": "N8GMZ"})
        uid = [u for u in owner.get("/api/users").get_json()["users"] if u["username"] == "bob"][0]["id"]
        old = _row(fresh_db, "bob")["sip_secret"]
        assert owner.put(f"/api/users/{uid}", json={"role": "user", "listen_only": True}).status_code == 200
        row = _row(fresh_db, "bob")
        assert row["listen_only"] == 1
        assert row["callsign"] == "N8GMZ"            # identity kept for when the flag comes off
        assert row["sip_secret"] != old              # ...but the issued credential is dead
        assert _isolated_sip["hangup"] == ["henwen-u-n8gmz"]

    def test_resending_listen_only_on_does_not_rotate_again(self, owner, fresh_db, _isolated_sip):
        owner.post("/api/users", json={"username": "bob", "password": "bobs-password-1",
                                       "role": "user", "callsign": "N8GMZ", "listen_only": True})
        uid = [u for u in owner.get("/api/users").get_json()["users"] if u["username"] == "bob"][0]["id"]
        old = _row(fresh_db, "bob")["sip_secret"]
        assert owner.put(f"/api/users/{uid}", json={"role": "user", "listen_only": True}).status_code == 200
        assert _row(fresh_db, "bob")["sip_secret"] == old
        assert _isolated_sip["hangup"] == []


class TestConnectGateIgnoresSessionLinks:
    def _kiosk(self, client, create_user):
        create_user("owner1", role="owner")
        create_user("kiosk1", password="kiosk1s-password1", role="user")
        _login(client, "kiosk1", "kiosk1s-password1")

    def test_own_listening_session_does_not_block_a_first_connect(self, client, create_user, monkeypatch,
                                                                 _session_callsigns):
        self._kiosk(client, create_user)
        monkeypatch.setattr(app, "get_cached_status", lambda n: {"connected": ["N8GMZ", "0HenWen-Web"]})
        sent = []
        class Ami:
            def rpt_cmd(self, node, cmd):
                sent.append(cmd); return {"success": True, "raw": ""}
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(Ami()))
        monkeypatch.setattr(app, "is_any_node_locked", lambda: False)
        r = client.post("/api/status/connect", json={"local_node": "643930", "remote_node": "27664"})
        assert r.status_code != 403, r.get_json()
        assert any("27664" in c for c in sent)

    def test_a_real_link_still_blocks(self, client, create_user, monkeypatch, _session_callsigns):
        self._kiosk(client, create_user)
        monkeypatch.setattr(app, "get_cached_status", lambda n: {"connected": ["N8GMZ", "27664"]})
        r = client.post("/api/status/connect", json={"local_node": "643930", "remote_node": "2000"})
        assert r.status_code == 403


class TestBoardSessionLabel:
    """A session link is in no node database, so the kiosk's callsign slot
    would show a bare dash; the board labels it instead."""

    def test_session_link_gets_a_label_and_a_node_does_not(self, client, create_user,
                                                          monkeypatch, _session_callsigns):
        create_user("owner1", role="owner")
        with open(app.RPT_CONF_PATH, "w") as f:
            f.write("[643930]\nrxchannel = SimpleUSB/643930\n")
        monkeypatch.setattr(app, "get_cached_status",
                            lambda n: {"connected": ["N8GMZ", "27664"], "links": {}})
        looked_up = []
        monkeypatch.setattr(app, "lookup_node",
                            lambda n: looked_up.append(n) or {"callsign": "W1AW", "desc": "", "location": ""})
        nodes = client.get("/api/status/board").get_json()["nodes"]
        rows = {c["node"]: c for c in nodes[0]["connected"]}
        assert rows["N8GMZ"]["session_label"] == "643930 HenWen User"
        assert rows["N8GMZ"]["callsign"] == ""
        assert rows["27664"]["session_label"] == ""
        assert rows["27664"]["callsign"] == "W1AW"
        assert "N8GMZ" not in looked_up      # no pointless allmondb miss per refresh
