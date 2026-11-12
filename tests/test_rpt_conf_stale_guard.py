"""rpt.conf stale-save guard (docs/ASL-WEB-UI-PLAN.md, hazard 3).

The ASL Web UI writes rpt.conf too. Every HenWen read of it carries a
conf_hash; a save that sends back base_hash is refused with 409 when the file
changed in between, and nothing is written. Since phase W3 HenWen saves only
macro and schedule stanzas, so those are what the guard protects.
"""
import os

import pytest

import app

CONF = """[general]

[643930]
rxchannel = dahdi/pseudo
idtime = 540000

[functions]
1 = ilink,1

[macro]
1 = *32000

[schedule]
1 = 00 00 * * *
"""

CHANGE = {"section": "macro", "changes": {"2": {"value": "*32001", "enabled": True}}}


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


def _external_edit(conf):
    """What an ASL Web UI save looks like from HenWen's side."""
    conf.write_text(conf.read_text().replace("idtime = 540000", "idtime = 600000"))


def test_reads_carry_the_hash(owner):
    client, conf = owner
    h = app.conf_hash(CONF)
    assert client.get("/api/conf").get_json()["conf_hash"] == h
    assert client.get("/api/conf/macro/macro").get_json()["conf_hash"] == h
    assert client.get("/api/conf/schedule/schedule").get_json()["conf_hash"] == h


def test_save_against_current_version_succeeds(owner):
    client, conf = owner
    r = client.post("/api/save", json=dict(CHANGE, base_hash=app.conf_hash(CONF)))
    assert r.status_code == 200
    assert r.get_json()["conf_hash"] == app.conf_hash(conf.read_text())
    assert "*32001" in conf.read_text()


def test_save_from_stale_page_is_refused(owner):
    client, conf = owner
    base = client.get("/api/conf/macro/macro").get_json()["conf_hash"]
    _external_edit(conf)
    r = client.post("/api/save", json=dict(CHANGE, base_hash=base))
    assert r.status_code == 409
    body = r.get_json()
    assert body["conflict"] is True and "ASL Web UI" in body["error"]
    text = conf.read_text()
    assert "idtime = 600000" in text and "*32001" not in text


def test_save_without_base_hash_is_unchecked(owner):
    """Scripts and older pages that don't send a version keep working."""
    client, conf = owner
    _external_edit(conf)
    r = client.post("/api/save", json=CHANGE)
    assert r.status_code == 200
    text = conf.read_text()
    assert "*32001" in text and "idtime = 600000" in text


def test_manager_page_keeps_the_guard_wired():
    """No JS test harness here, so pin the invariants in the Manager source:
    the list editors record the version they loaded, a save sends it back,
    and a reload doesn't re-baseline an editor with unsaved changes."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "henwen-manager.html")).read()
    assert "base_hash: _confHash[section]" in src
    assert "_confHashLoaded(name, r.data.conf_hash);" in src
    loaded = src.split("function _confHashLoaded(section, hash) {", 1)[1].split("}\n", 1)[0]
    assert "pendingChanges[section]" in loaded and "return;" in loaded
