"""HenWen's own AMI user (docs/ASL-WEB-UI-PLAN.md, hazard 1).

The ASL Web UI's AMI Secret page rewrites exactly [admin] in manager.conf
and updates only Allmon3, so HenWen must not depend on [admin]. These cover
the app side (which manager.conf user is chosen, the shared-user warning) and
the manager.conf parser ami-setup.sh runs, extracted from the script the same
way test_dvswitch_apply_script.py does -- the whole script edits
/etc/asterisk and restarts services, so it is never executed here.
"""
import os
import re
import subprocess
import sys

import app as henwen_app

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "ami-setup.sh")
SRC = open(SCRIPT).read()


def _creds(monkeypatch, tmp_path, conf, env_user="", env_secret=""):
    p = tmp_path / "manager.conf"
    p.write_text(conf)
    monkeypatch.setattr(henwen_app, "MANAGER_CONF", str(p))
    monkeypatch.setenv("AMI_USER", env_user)
    monkeypatch.setenv("AMI_SECRET", env_secret)
    return henwen_app.parse_manager_conf()


class TestParseManagerConf:
    def test_dedicated_user_wins_over_first_stanza(self, monkeypatch, tmp_path):
        c = _creds(monkeypatch, tmp_path,
                   "[general]\nenabled=yes\n\n[admin]\nsecret=adminpw\n\n"
                   "[henwen]\nsecret = hwpw ; comment\nread=all\n")
        assert (c["user"], c["secret"]) == ("henwen", "hwpw")

    def test_falls_back_to_first_user_without_dedicated(self, monkeypatch, tmp_path):
        c = _creds(monkeypatch, tmp_path, "[general]\n[admin]\nsecret=adminpw\n")
        assert (c["user"], c["secret"]) == ("admin", "adminpw")

    def test_disabled_dedicated_user_is_skipped(self, monkeypatch, tmp_path):
        c = _creds(monkeypatch, tmp_path,
                   "[general]\n[admin]\nsecret=a\n[henwen]\nsecret=h\nenabled=no\n")
        assert c["user"] == "admin"

    def test_env_vars_still_take_priority(self, monkeypatch, tmp_path):
        c = _creds(monkeypatch, tmp_path, "[henwen]\nsecret=h\n",
                   env_user="other", env_secret="realsecret")
        assert (c["user"], c["secret"]) == ("other", "realsecret")


class TestSharedUserWarning:
    def test_admin_warns(self):
        w = henwen_app.ami_shared_user_warning("Admin")
        assert w and "ami-setup.sh" in w and "henwen" in w

    def test_others_do_not(self):
        assert henwen_app.ami_shared_user_warning("henwen") is None
        assert henwen_app.ami_shared_user_warning(None) is None

    def test_ami_test_route_reports_it(self, client, create_user, monkeypatch):
        row = create_user("owner1", role="owner")
        with client.session_transaction() as sess:
            sess.update(logged_in=True, username="owner1", role="owner", user_id=row["id"],
                        password_epoch=row["password_epoch"], sid="t-owner1",
                        idle_timeout=henwen_app.SESSION_IDLE_TIMEOUT)
        monkeypatch.setattr(henwen_app, "parse_manager_conf",
                            lambda: {"host": "127.0.0.1", "port": 5038, "user": "admin", "secret": "x"})
        monkeypatch.setattr(henwen_app, "_ami_ensure_connected",
                            lambda: (_ for _ in ()).throw(Exception("down")))
        monkeypatch.setattr(henwen_app.subprocess, "run", lambda *a, **k: None)
        monkeypatch.setattr(henwen_app.time, "sleep", lambda s: None)
        resp = client.get("/api/ami/test")
        assert "ASL Web UI" in resp.get_json()["warning"]


def _script_parser(tmp_path, conf):
    m = re.search(r"AMI_INFO=\$\(python3 - \"\$HENWEN_AMI_USER\" <<'PYEOF'\n(.*?)\nPYEOF", SRC, re.S)
    assert m, "parser heredoc not found in ami-setup.sh"
    conf_path = tmp_path / "manager.conf"
    conf_path.write_text(conf)
    py = tmp_path / "parse.py"
    py.write_text(m.group(1).replace("/etc/asterisk/manager.conf", str(conf_path)))
    out = subprocess.run([sys.executable, "-I", str(py), "henwen"],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split())


class TestAmiSetupScript:
    def test_syntax(self):
        subprocess.run(["bash", "-n", SCRIPT], check=True)

    def test_never_picks_admin(self, tmp_path):
        r = _script_parser(tmp_path, "[general]\nport=5039\n[admin]\nsecret=adminpw\n")
        assert r == {"ENABLED": "yes", "PORT": "5039", "HAVE_STANZA": "no"}

    def test_reads_existing_dedicated_secret(self, tmp_path):
        r = _script_parser(tmp_path, "[admin]\nsecret=a\n[henwen]\nsecret = Ab9_x ; c\n")
        assert r["HAVE_STANZA"] == "yes" and r["SECRET"] == "'Ab9_x'"

    def test_unsafe_secret_is_reported_not_used(self, tmp_path):
        r = _script_parser(tmp_path, "[henwen]\nsecret=a'b|c\n")
        assert r["HAVE_STANZA"] == "yes" and "SECRET" not in r

    def test_new_stanza_is_loopback_only_and_has_event_classes(self):
        block = SRC.split("cat >> \"$MANAGER_CONF\" << AMIEOF", 1)[1].split("AMIEOF", 1)[0]
        assert "[$USER]" in block
        assert "deny = 0.0.0.0/0.0.0.0" in block
        assert "permit = 127.0.0.1/255.255.255.255" in block
        read = re.search(r"^read = (.*)$", block, re.M).group(1).split(",")
        assert "dtmf" in read and "call" in read  # start_sip_keyed_monitor()

    def test_installer_names_its_unit(self):
        """PR #13 review: with both HenWen and HenWen2 units present,
        auto-detection would pick HenWen2 while install.sh set up HenWen."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "install.sh")).read()
        assert 'HENWEN_SERVICE="$SERVICE_NAME" bash "$INSTALL_DIR/ami-setup.sh"' in src

    def test_unit_file_is_only_rewritten_after_a_successful_login(self):
        """Review of PR #13: a login that still failed fell through to the
        step that rewrites AMI_USER/AMI_SECRET and restarts the service."""
        guard = SRC.index('if [ "${RESULT}" != "success" ]; then')
        rewrite = SRC.index('sed -i "s|^Environment=\\"AMI_USER=')
        assert SRC.index("Testing AMI login") < guard < rewrite
        assert "exit 1" in SRC[guard:rewrite]

    def test_hints_name_the_unit_the_script_set_up(self):
        """On a HenWen2 box the closing hint said `journalctl -u HenWen`."""
        assert "journalctl -u HenWen " not in SRC
        assert 'journalctl -u $SERVICE_NAME -f' in SRC

    def test_secrets_are_not_printed(self, tmp_path):
        """The script echoed manager.conf verbatim and the new secret in its
        closing summary; both end up in terminals and install logs."""
        assert 'cat "$MANAGER_CONF"' not in SRC
        assert not re.search(r'echo[^\n]*\$\{?SECRET\}?[^:+]', SRC)
        sed = re.search(r"sed -E '([^']+)' \"\$MANAGER_CONF\"", SRC).group(1)
        conf = tmp_path / "manager.conf"
        conf.write_text("[admin]\nsecret = hunter2 ; note\n Secret=abc123\n;secret = old\nread = all\n")
        out = subprocess.run(["sed", "-E", sed, str(conf)], capture_output=True, text=True, check=True).stdout
        assert "hunter2" not in out and "abc123" not in out
        assert "secret = (hidden) ; note" in out and "read = all" in out
