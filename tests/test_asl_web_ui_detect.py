"""ASL Web UI detection (docs/ASL-WEB-UI-PLAN.md, phase W1)."""
import os

import pytest

import app


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    www = tmp_path / "www"
    sites = tmp_path / "sites"
    sites.mkdir()
    monkeypatch.setattr(app, "ASL_WEB_UI_WWW", str(www))
    monkeypatch.setattr(app, "ASL_WEB_UI_SOCKET", str(tmp_path / "web.sock"))
    monkeypatch.setattr(app, "ASL_WEB_UI_GLOBAL_CONF", str(tmp_path / "conf-enabled.conf"))
    monkeypatch.setattr(app, "ASL_WEB_UI_APPLY_SCRIPT", str(tmp_path / "apply.sh"))
    monkeypatch.setattr(app, "HENWEN_APACHE_SITES_DIR", str(sites))
    monkeypatch.setattr(app, "HENWEN_APACHE_VHOST_CANDIDATES", ())
    app.asl_web_ui_status.cache_clear()
    yield tmp_path
    app.asl_web_ui_status.cache_clear()


def _install(p):
    (p / "www").mkdir()
    (p / "www" / "index.html").write_text("<html>")


def _vhost(p, name="henwen.conf", scoped=False):
    lines = ["<VirtualHost *:443>"]
    if scoped:
        lines += ["    # HenWen: ASL Web UI.", "    ProxyPass /asl-web-ui !"]
    lines += [f"    ProxyPass        / http://127.0.0.1:{app.PORT}/ retry=0 timeout=120", "</VirtualHost>"]
    (p / "sites" / name).write_text("\n".join(lines) + "\n")


def test_not_installed(paths):
    s = app.asl_web_ui_status()
    assert s["installed"] is False and s["url"] is None and s["overlap"] == {}


def test_global_conf_is_reachable_and_suggests_scoping(paths):
    _install(paths)
    (paths / "conf-enabled.conf").write_text("Alias ...")
    (paths / "apply.sh").write_text("#!/bin/bash\n")
    _vhost(paths)
    s = app.asl_web_ui_status()
    assert s["reachable"] and s["url"] == "/asl-web-ui/"
    assert s["apply_script"] == str(paths / "apply.sh")
    assert "raw-editor" in s["overlap"] and s["running"] is False


def test_scoped_everywhere_needs_no_fix(paths):
    _install(paths)
    (paths / "apply.sh").write_text("")
    _vhost(paths, scoped=True)
    (paths / "web.sock").write_text("")
    s = app.asl_web_ui_status()
    assert s["reachable"] and s["running"] and s["apply_script"] is None


def test_partly_scoped_is_not_reachable(paths):
    """With the global conf off, a HenWen vhost without the include sends
    /asl-web-ui/ to Flask, so a link from it would 404."""
    _install(paths)
    _vhost(paths, "a.conf", scoped=True)
    _vhost(paths, "b.conf", scoped=False)
    s = app.asl_web_ui_status()
    assert s["reachable"] is False and s["url"] is None
    assert [os.path.basename(v) for v in s["vhosts_missing"]] == ["b.conf"]


def test_no_apply_hint_when_script_absent(paths):
    _install(paths)
    (paths / "conf-enabled.conf").write_text("")
    assert app.asl_web_ui_status()["apply_script"] is None


def _login(client, create_user, role):
    if role != "owner":
        create_user("own", role="owner")
    row = create_user("u-" + role, role=role)
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="u-" + role, role=role, user_id=row["id"],
                    password_epoch=row["password_epoch"], sid="t-" + role,
                    idle_timeout=app.SESSION_IDLE_TIMEOUT)


def test_route_is_admin_plus(client, create_user, paths):
    _login(client, create_user, "admin")
    assert client.get("/api/asl-web-ui/status").status_code == 200


def test_route_refuses_plain_users(client, create_user, paths):
    _login(client, create_user, "user")
    assert client.get("/api/asl-web-ui/status").status_code in (302, 401, 403)


def test_overlap_routes_are_hash_routes():
    for page, o in app.ASL_WEB_UI_OVERLAP.items():
        assert o["route"].startswith("#/"), page


def test_manager_picks_a_real_node_page_for_node_routes():
    """PR #17 review: page-node-control matches the page-node- prefix first."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "henwen-manager.html")).read()
    assert "document.querySelector('section[id^=\"page-node-\"]')" not in src
    assert "if (!firstNode) firstNode = _aslPageKey(sec.id).node;" in src
