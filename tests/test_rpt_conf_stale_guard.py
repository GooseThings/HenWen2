"""rpt.conf stale-save guard (docs/ASL-WEB-UI-PLAN.md, hazard 3).

The ASL Web UI writes rpt.conf too. Every HenWen read of it carries a
conf_hash; a save that sends back base_hash is refused with 409 when the file
changed in between, and nothing is written.
"""
import pytest

import app

CONF = """[general]
node_lookup_method = both

[643930]
rxchannel = dahdi/pseudo
duplex = 0
idtime = 540000
"""


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
    assert client.get("/api/conf/node/643930").get_json()["conf_hash"] == h
    assert client.get("/api/backups").get_json()["conf_hash"] == h


def test_raw_save_against_current_version_succeeds(owner):
    client, conf = owner
    new = CONF + "\n; edited\n"
    r = client.post("/api/save", json={"raw_content": new, "base_hash": app.conf_hash(CONF)})
    assert r.status_code == 200
    assert r.get_json()["conf_hash"] == app.conf_hash(new)
    assert conf.read_text() == new


def test_raw_save_from_stale_page_is_refused(owner):
    client, conf = owner
    base = client.get("/api/conf").get_json()["conf_hash"]
    _external_edit(conf)
    r = client.post("/api/save", json={"raw_content": CONF, "base_hash": base})
    assert r.status_code == 409
    body = r.get_json()
    assert body["conflict"] is True and "ASL Web UI" in body["error"]
    assert "idtime = 600000" in conf.read_text()   # the newer edit survived


def test_stanza_save_from_stale_page_is_refused(owner):
    client, conf = owner
    base = client.get("/api/conf/node/643930").get_json()["conf_hash"]
    _external_edit(conf)
    r = client.post("/api/save", json={
        "section": "643930", "changes": {"duplex": {"value": "1", "enabled": True}},
        "base_hash": base})
    assert r.status_code == 409
    assert "duplex = 0" in conf.read_text()


def test_save_without_base_hash_is_unchecked(owner):
    """Scripts and older pages that don't send a version keep working."""
    client, conf = owner
    _external_edit(conf)
    r = client.post("/api/save", json={
        "section": "643930", "changes": {"duplex": {"value": "1", "enabled": True}}})
    assert r.status_code == 200
    text = conf.read_text()
    assert "duplex = 1" in text and "idtime = 600000" in text


def test_restore_from_stale_page_is_refused(owner):
    client, conf = owner
    first = client.post("/api/save", json={"raw_content": CONF + "; v2\n"})
    name = first.get_json()["backup"].rsplit("/", 1)[-1]
    base = client.get("/api/backups").get_json()["conf_hash"]
    _external_edit(conf)
    r = client.post(f"/api/backups/{name}/restore", json={"base_hash": base})
    assert r.status_code == 409
    assert "idtime = 600000" in conf.read_text()
