"""Node-number drift (docs/ASL-WEB-UI-PLAN.md, hazard 4).

The ASL Web UI renames a node across rpt.conf, AstDB and Allmon3 but not
HenWen's rows. node_drift_report() finds HenWen settings naming a local node
rpt.conf no longer has; /api/node-drift/repoint moves them.
"""
import pytest

import app

CONF_AFTER_RENAME = "[general]\n\n[643931]\nrxchannel = dahdi/pseudo\n"


@pytest.fixture(autouse=True)
def _no_sip_dialplan(tmp_path, monkeypatch):
    """Other tests write a real dialplan into the shared test root."""
    monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "no-dialplan.conf"))


@pytest.fixture()
def admin(client, create_user, tmp_path, monkeypatch):
    conf = tmp_path / "rpt.conf"
    conf.write_text(CONF_AFTER_RENAME)
    monkeypatch.setattr(app, "RPT_CONF_PATH", str(conf))
    monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(tmp_path / "dialplan.conf"))
    app._conf_file_cache.clear()
    create_user("own", role="owner")   # first-run setup must be complete
    row = create_user("adm", role="admin")
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="adm", role="admin", user_id=row["id"],
                    password_epoch=row["password_epoch"], sid="t-adm",
                    idle_timeout=app.SESSION_IDLE_TIMEOUT)
    return client, tmp_path


def _seed(db, old="643930", new="643931"):
    db.execute("INSERT INTO connectors (name, local_node, target_node) VALUES ('Net A', ?, '2000')", (old,))
    db.execute("INSERT INTO connectors (name, local_node, target_node) VALUES ('Fine', ?, '2000')", (new,))
    db.execute("INSERT INTO id_configs (name, node) VALUES ('ID', ?)", (old,))
    db.execute("INSERT INTO permanent_links (local_node, peer_node) VALUES (?, '2000')", (old,))
    db.execute("INSERT INTO permanent_links (local_node, peer_node) VALUES (?, '2000')", (new,))
    db.execute("INSERT INTO node_lockouts (node) VALUES (?)", (old,))
    db.execute("INSERT INTO favorites (user_id, node) VALUES (1, ?)", (old,))   # a remote node: never drift
    db.commit()


def test_report_lists_only_local_references_to_missing_nodes(fresh_db):
    _seed(fresh_db)
    stale = app.node_drift_report(fresh_db, ["643931"])
    assert list(stale) == ["643930"]
    labels = {u["label"]: u for u in stale["643930"]}
    assert set(labels) == {"Smart Connector", "Node ID", "Permanent links", "Node lockout"}
    assert labels["Smart Connector"]["names"] == ["Net A"]


def test_nothing_is_stale_when_rpt_conf_is_unreadable(fresh_db):
    _seed(fresh_db)
    assert app.node_drift_report(fresh_db, []) == {}


def test_sip_dialplan_counts(fresh_db, tmp_path, monkeypatch):
    plan = tmp_path / "dialplan.conf"
    plan.write_text(app.render_user_dialplans([("N8GMZ", "643930")]))
    monkeypatch.setattr(app, "SIP_USERS_DIALPLAN", str(plan))
    stale = app.node_drift_report(fresh_db, ["643931"])
    assert stale["643930"][0]["label"] == "Per-user SIP dialplan"


def test_repoint_moves_rows_and_keeps_existing_keys(fresh_db):
    _seed(fresh_db)
    moved, skipped = app.node_drift_repoint(fresh_db, "643930", "643931", "owner")
    assert skipped == []
    assert moved["connectors"] == 1 and moved["id_configs"] == 1
    assert app.node_drift_report(fresh_db, ["643931"]) == {}
    # The (643931, 2000) link already existed: one row, not a duplicate.
    assert fresh_db.execute("SELECT COUNT(*) FROM permanent_links").fetchone()[0] == 1
    assert fresh_db.execute("SELECT node FROM favorites").fetchone()[0] == "643930"


def test_api_report_and_repoint(admin):
    client, _ = admin
    _seed(app.get_db())
    # A lockout row, even a stale one, makes every non-owner read-only
    # (check_auth answers 423); see test_owner_clears_a_stale_lockout.
    app.get_db().execute("DELETE FROM node_lockouts"); app.get_db().commit()
    body = client.get("/api/node-drift").get_json()
    assert body["local_nodes"] == ["643931"] and "643930" in body["stale"]
    r = client.post("/api/node-drift/repoint", json={"from": "643930", "to": "643931"})
    body = r.get_json()
    assert r.status_code == 200 and body["skipped"] == ["Node ID"]   # superuser+ (/api/id)
    assert [u["label"] for u in body["stale"]["643930"]] == ["Node ID"]


@pytest.mark.parametrize("payload", [
    {"from": "643930", "to": "999999"},   # target not on this box
    {"from": "643931", "to": "643931"},   # source still exists
    {"from": "x; DROP", "to": "643931"},
])
def test_api_repoint_rejects_bad_input(admin, payload):
    client, _ = admin
    assert client.post("/api/node-drift/repoint", json=payload).status_code == 400


def test_plain_users_cannot_repoint(client, create_user):
    create_user("own", role="owner")
    row = create_user("u1", role="user")
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="u1", role="user", user_id=row["id"],
                    password_epoch=row["password_epoch"], sid="t-u1",
                    idle_timeout=app.SESSION_IDLE_TIMEOUT)
    r = client.post("/api/node-drift/repoint", json={"from": "643930", "to": "643931"})
    assert r.status_code in (302, 401, 403)


def test_owner_clears_a_stale_lockout(client, create_user, tmp_path, monkeypatch):
    """A lockout on a node that was renamed away still locks the whole system
    for non-owners; the owner is the one who can re-point it."""
    conf = tmp_path / "rpt.conf"
    conf.write_text(CONF_AFTER_RENAME)
    monkeypatch.setattr(app, "RPT_CONF_PATH", str(conf))
    app._conf_file_cache.clear()
    row = create_user("own", role="owner")
    _seed(app.get_db())
    with client.session_transaction() as sess:
        sess.update(logged_in=True, username="own", role="owner", user_id=row["id"],
                    password_epoch=row["password_epoch"], sid="t-own",
                    idle_timeout=app.SESSION_IDLE_TIMEOUT)
    r = client.post("/api/node-drift/repoint", json={"from": "643930", "to": "643931"})
    assert r.status_code == 200
    assert app.get_db().execute("SELECT node FROM node_lockouts").fetchone()[0] == "643931"


def test_admin_repoint_leaves_owner_only_settings(fresh_db):
    """Security review on PR #16: the stream relay config and node lockouts
    are owner-only to edit, so re-pointing must not be a way around that."""
    _seed(fresh_db)
    fresh_db.execute("UPDATE stream_relay_config SET target_node='643930'")
    if not fresh_db.execute("SELECT 1 FROM stream_relay_config").fetchone():
        fresh_db.execute("INSERT INTO stream_relay_config (id, target_node) VALUES (1, '643930')")
    fresh_db.commit()
    moved, skipped = app.node_drift_repoint(fresh_db, "643930", "643931", "admin")
    assert set(skipped) == {"Node lockout", "Stream relay", "Node ID"}
    assert "node_lockouts" not in moved and "stream_relay_config" not in moved
    assert fresh_db.execute("SELECT node FROM node_lockouts").fetchone()[0] == "643930"
    assert moved["connectors"] == 1


def test_superuser_moves_node_id_but_not_owner_settings(fresh_db):
    """Second security review on PR #16: /api/id is superuser+."""
    _seed(fresh_db)
    moved, skipped = app.node_drift_repoint(fresh_db, "643930", "643931", "superuser")
    assert moved["id_configs"] == 1 and skipped == ["Node lockout"]


def test_unknown_role_moves_nothing(fresh_db):
    _seed(fresh_db)
    moved, skipped = app.node_drift_repoint(fresh_db, "643930", "643931", None)
    assert moved == {} and "Smart Connector" in skipped
