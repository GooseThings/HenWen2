"""Tests for the Manager > Asterisk Updates check: apt output parsing, the
weekly-check state machine and the owner-gated routes. Installing was handed
to Cockpit/the ASL Web UI (docs/ASL-WEB-UI-PLAN.md, decision 2). Nothing here
touches apt, sudo or systemd -- _asl_run is stubbed."""
import json
import subprocess

import pytest

import app

POLICY = """asl3-asterisk:
  Installed: 2:22.9.0+asl3-3.9.3-1.deb13
  Candidate: 2:22.10.1+asl3-3.10.5-1.deb13
  Version table:
 *** 2:22.9.0+asl3-3.9.3-1.deb13 100
        100 /var/lib/dpkg/status
     2:22.10.1+asl3-3.10.5-1.deb13 500
        500 https://repo.allstarlink.org/public trixie/main amd64 Packages
asl3-asterisk-config:
  Installed: 2:22.9.0+asl3-3.9.3-1.deb13
  Candidate: 2:22.9.0+asl3-3.9.3-1.deb13
"""

SIM = """Reading package lists...
Inst asl3-asterisk [2:22.9.0+asl3-3.9.3-1.deb13] (2:22.10.1+asl3-3.10.5-1.deb13 AllStarLink:trixie [amd64])
Inst asl3-asterisk-modules [2:22.9.0+asl3-3.9.3-1.deb13] (2:22.10.1+asl3-3.10.5-1.deb13 AllStarLink:trixie [amd64])
Inst libnewdep (1.0 AllStarLink:trixie [amd64])
Conf asl3-asterisk (2:22.10.1+asl3-3.10.5-1.deb13 AllStarLink:trixie [amd64])
"""


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """/login carries a real rate limit whose in-memory storage outlives
    conftest's RATELIMIT_ENABLED=False (the limiter is built at import), so
    this file's many logins would otherwise trip it partway through."""
    app.limiter.reset()


def _login(client, username, password="password12345"):
    return client.post("/login", data={"username": username, "password": password})


class TestParsing:
    def test_policy(self):
        p = app.parse_apt_policy(POLICY)
        assert p["asl3-asterisk"] == ("2:22.9.0+asl3-3.9.3-1.deb13", "2:22.10.1+asl3-3.10.5-1.deb13")
        assert p["asl3-asterisk-config"][0] == p["asl3-asterisk-config"][1]

    def test_policy_none_is_normalised(self):
        p = app.parse_apt_policy("foo:\n  Installed: (none)\n  Candidate: 1.0\n")
        assert p["foo"] == (None, "1.0")

    def test_simulation_ignores_conf_lines_and_handles_new_deps(self):
        out = app.parse_apt_simulation(SIM)
        assert [u["name"] for u in out] == ["asl3-asterisk", "asl3-asterisk-modules", "libnewdep"]
        assert out[0]["old"] == "2:22.9.0+asl3-3.9.3-1.deb13"
        assert out[0]["new"] == "2:22.10.1+asl3-3.10.5-1.deb13"
        assert out[2]["old"] == "" and out[2]["new"] == "1.0"




@pytest.fixture()
def stubbed_apt(monkeypatch):
    """_asl_run answers dpkg-query / apt-cache / apt-get -s / the sudo check."""
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        if cmd[0] == "dpkg-query":
            out = ("ii \tasl3-asterisk\t2:22.9.0+asl3-3.9.3-1.deb13\n"
                   "ii \tasl3-asterisk-config\t2:22.9.0+asl3-3.9.3-1.deb13\n"
                   "rc \tasl3-asterisk-old\t1.0\n")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[0] == "apt-cache":
            return subprocess.CompletedProcess(cmd, 0, POLICY, "")
        if cmd[0] == "apt-get":
            return subprocess.CompletedProcess(cmd, 0, SIM, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")   # sudo ... check

    monkeypatch.setattr(app, "_asl_run", fake_run)
    return calls


class TestCollectInfo:
    def test_reports_upgrade_and_skips_removed_packages(self, stubbed_apt):
        info = app._asl_collect_update_info()
        assert info["available"] is True
        assert [p["name"] for p in info["packages"]] == ["asl3-asterisk", "asl3-asterisk-config"]
        assert [p["newer"] for p in info["packages"]] == [True, False]
        assert len(info["will_upgrade"]) == 3

    def test_up_to_date_does_not_simulate(self, stubbed_apt, monkeypatch):
        monkeypatch.setattr(app, "_asl_run", lambda cmd, t: subprocess.CompletedProcess(
            cmd, 0,
            "ii \tasl3-asterisk\t1.0\n" if cmd[0] == "dpkg-query"
            else "asl3-asterisk:\n  Installed: 1.0\n  Candidate: 1.0\n", ""))
        info = app._asl_collect_update_info()
        assert info["available"] is False and info["will_upgrade"] == []


class TestCheckState:
    def test_check_persists_state(self, fresh_db, stubbed_apt):
        state = app._asl_check_for_updates(refresh=True)
        assert state["available"] is True and state["refresh_ok"] is True
        assert any(c[0] == app.SUDO_PATH and c[-1] == "check" for c in stubbed_apt)
        assert app._asl_load_state()["will_upgrade"][0]["name"] == "asl3-asterisk"

    def test_failed_refresh_is_recorded_but_result_still_computed(self, fresh_db, stubbed_apt, monkeypatch):
        real = app._asl_run

        def run(cmd, timeout):
            if cmd[0] == app.SUDO_PATH:
                return subprocess.CompletedProcess(cmd, 1, "", "sudo: a password is required")
            return real(cmd, timeout)

        monkeypatch.setattr(app, "_asl_run", run)
        state = app._asl_check_for_updates(refresh=True)
        assert state["refresh_ok"] is False
        assert "password" in state["refresh_error"]
        assert state["available"] is True

    def test_second_concurrent_check_is_refused(self, fresh_db, stubbed_apt):
        assert app._asl_check_lock.acquire(blocking=False)
        try:
            assert app._asl_check_for_updates(refresh=False) is None
        finally:
            app._asl_check_lock.release()

    def test_corrupt_saved_state_reads_as_empty(self, fresh_db):
        app.set_setting(app.ASL_UPDATE_STATE_KEY, "{not json")
        assert app._asl_load_state() == {}


class TestRoutes:
    def _owner(self, client, create_user):
        create_user("owner1", role="owner")
        _login(client, "owner1")

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/asl-update/status"), ("post", "/api/asl-update/check"),
    ])
    def test_non_owner_is_refused(self, client, create_user, method, path):
        create_user("owner1", role="owner")
        create_user("admin1", password="admin1s-password1", role="superuser")
        _login(client, "admin1", "admin1s-password1")
        assert getattr(client, method)(path).status_code == 403









def test_install_half_is_gone():
    """Decision 2: no install route, no root install rule, script check-only."""
    import os
    root = os.path.join(os.path.dirname(__file__), "..")
    rules = {r.rule for r in app.app.url_map.iter_rules()}
    assert "/api/asl-update/install" not in rules and "/api/asl-update/log" not in rules
    assert "asl-asterisk-update.sh install" not in open(os.path.join(root, "provision-sudoers.sh")).read()
    script = open(os.path.join(root, "asl-asterisk-update.sh")).read()
    assert "apt-get install" not in script and "usage: $0 check" in script
