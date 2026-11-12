"""Phase W3 (docs/ASL-WEB-UI-PLAN.md): HenWen hands every rpt.conf edit but
macros and schedules to the ASL Web UI, and the pages that only duplicated
it are gone."""
import os

import pytest

import app

CONF = """[general]

[643930]
rxchannel = dahdi/pseudo
duplex = 0

[macro]
1 = *32000

[schedule]
1 = 00 00 * * *
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


@pytest.mark.parametrize("body", [
    {"raw_content": "[general]\n"},
    {"section": "643930", "changes": {"duplex": {"value": "1", "enabled": True}}},
    {"section": "general", "changes": {"node_lookup_method": {"value": "dns", "enabled": True}}},
    {"section": "node-main", "changes": {"totime": {"value": "1", "enabled": True}}},
])
def test_everything_but_macros_and_schedules_is_refused(owner, body):
    client, conf = owner
    r = client.post("/api/save", json=body)
    assert r.status_code == 400 and r.get_json()["handed_over"] is True
    assert conf.read_text() == CONF


@pytest.mark.parametrize("section,key,value", [("macro", "2", "*32001"), ("schedule", "2", "30 6 * * 1")])
def test_macros_and_schedules_still_save(owner, section, key, value):
    client, conf = owner
    r = client.post("/api/save", json={"section": section,
                                       "changes": {key: {"value": value, "enabled": True}}})
    assert r.status_code == 200 and value in conf.read_text()


def test_removed_routes_are_gone():
    rules = {r.rule for r in app.app.url_map.iter_rules()}
    for gone in ("/api/conf/node/<node>", "/api/conf/templates", "/api/conf/template/<name>",
                 "/api/backups", "/api/backups/<name>/restore", "/api/backup/<filename>",
                 "/api/asterisk/command", "/api/asterisk/log", "/api/asterisk/verbose"):
        assert gone not in rules, gone


def test_removed_pages_are_gone():
    src = open(os.path.join(os.path.dirname(__file__), "..", "templates", "henwen-manager.html")).read()
    for page in ("page-general", "page-raw-editor", "page-backups", "page-ast-console",
                 'id="page-node-{{ node }}"', 'id="page-template-{{ tmpl }}"'):
        assert page not in src, page
