"""Coexistence with the ASL Web UI on one box (docs/ASL-WEB-UI-PLAN.md).

Hazard 2 -- asl-web-ui/apply.sh.

The script itself edits /etc/apache2 and reloads Apache as root, so it is
never run here. These run its real vhost insertion -- apache-common.sh's
henwen_insert_before_proxypass() with the script's own INSERT_TEXT -- on a
scratch vhost, and, where an apache2 binary is installed, syntax-check the
result. The routing itself (Alias + "ProxyPass /asl-web-ui !" beats HenWen's
catch-all; without the "!" line Flask gets /asl-web-ui/) was verified once
against a throwaway Apache 2.4.69 instance on a high port, see the PR.
"""
import os
import re
import shutil
import subprocess

import pytest

import app

ROOT = os.path.join(os.path.dirname(__file__), "..")
APPLY = os.path.join(ROOT, "asl-web-ui", "apply.sh")
ROLLBACK = os.path.join(ROOT, "asl-web-ui", "rollback.sh")
COMMON = os.path.join(ROOT, "apache-common.sh")
SRC = open(APPLY).read()
PORT = 5000

VHOST = f"""<VirtualHost *:443>
    ServerName henwen.example.com
    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:{PORT}/ retry=0 timeout=120
    ProxyPassReverse / http://127.0.0.1:{PORT}/
</VirtualHost>
"""


def _patch(tmp_path, vhost_text=VHOST, conf_available="/etc/apache2/conf-available/asl-web-ui.conf"):
    vhost = tmp_path / "henwen.conf"
    vhost.write_text(vhost_text)
    marker = re.search(r'^MARKER="([^"]+)"', SRC, re.M).group(1)
    insert = re.search(r'^INSERT_TEXT="(.*?)^"\n', SRC, re.M | re.S).group(0)
    script = (f'. "{COMMON}"\nMARKER="{marker}"\nCONF_AVAILABLE="{conf_available}"\n'
              f'{insert}henwen_insert_before_proxypass_each_block "{vhost}" {PORT} "$MARKER" "$INSERT_TEXT"\n')
    rc = subprocess.run(["bash", "-c", script]).returncode
    return rc, vhost.read_text()


def test_scripts_parse():
    for path in (APPLY, ROLLBACK):
        subprocess.run(["bash", "-n", path], check=True)


def test_inserts_above_the_catch_all_in_order(tmp_path):
    rc, out = _patch(tmp_path)
    assert rc == 0
    lines = [l.strip() for l in out.splitlines()]
    inc = lines.index("IncludeOptional /etc/apache2/conf-available/asl-web-ui.conf")
    bang = lines.index("ProxyPass /asl-web-ui !")
    catch = next(i for i, l in enumerate(lines) if l.startswith("ProxyPass        /"))
    # The include carries ProxyPass /asl-web-ui/api/, which must match before
    # the "!" exclusion, which must match before HenWen's catch-all.
    assert inc < bang < catch


def test_rerun_is_a_no_op(tmp_path):
    rc, once = _patch(tmp_path)
    rc2, twice = _patch(tmp_path, vhost_text=once)
    assert rc == 0 and rc2 == 1 and once == twice


def test_vhost_without_catch_all_is_a_failure(tmp_path):
    rc, out = _patch(tmp_path, vhost_text="<VirtualHost *:443>\n</VirtualHost>\n")
    assert rc == 2 and "asl-web-ui" not in out


TWO_BLOCKS = VHOST.replace("*:443", "*:80").replace("henwen.example.com", "a.example.com") + VHOST


def test_every_henwen_block_in_one_file_is_patched(tmp_path):
    """PR #14 review: a file-wide marker check patched only the first block."""
    rc, out = _patch(tmp_path, vhost_text=TWO_BLOCKS)
    assert rc == 0
    for block in out.split("</VirtualHost>")[:2]:
        lines = [l.strip() for l in block.splitlines()]
        assert lines.index("ProxyPass /asl-web-ui !") < next(
            i for i, l in enumerate(lines) if l.startswith("ProxyPass        /"))
    assert out.count("ProxyPass /asl-web-ui !") == 2


def test_a_block_added_later_gets_patched_on_rerun(tmp_path):
    rc, once = _patch(tmp_path)
    rc2, twice = _patch(tmp_path, vhost_text=once + VHOST.replace("henwen.example.com", "b.example.com"))
    assert rc2 == 0 and twice.count("ProxyPass /asl-web-ui !") == 2


def test_non_henwen_blocks_are_left_alone(tmp_path):
    other = "<VirtualHost *:443>\n    ServerName other.example.com\n    DocumentRoot /var/www\n</VirtualHost>\n"
    rc, out = _patch(tmp_path, vhost_text=other + VHOST)
    assert rc == 0 and out.startswith(other) and out.count("asl-web-ui !") == 1


def test_section_tags_match_case_insensitively(tmp_path):
    """PR #14 review: Apache accepts <virtualhost>, so must the patcher."""
    rc, out = _patch(tmp_path, vhost_text=VHOST.replace("VirtualHost", "virtualHost"))
    assert rc == 0 and "ProxyPass /asl-web-ui !" in out


def test_backup_dir_is_unique_per_run():
    """PR #14 review: two runs in one second must not share a backup dir."""
    assert 'BACKUP_DIR=$(mktemp -d "/root/henwen-asl-web-ui-backup-${STAMP}-XXXXXX")' in SRC


def test_setup_https_lan_rewrite_carries_the_rules(tmp_path):
    """PR #14 review: setup-https.sh --lan rebuilds the SSL vhost and keeps
    only the lines its CARRY grep selects."""
    src = open(os.path.join(ROOT, "tx-spike", "setup-https.sh")).read()
    pattern = re.search(r"CARRY=\$\(grep -E '([^']+)' \"\$SSL_AVAIL\"", src).group(1)
    _, patched = _patch(tmp_path)
    patched = patched.replace("    ProxyPreserveHost On\n",
                              "    # HenWen low-latency RX audio: x\n    ProxyPass /ws-audio ws://127.0.0.1:8098/ retry=0\n")
    (tmp_path / "ssl.conf").write_text(patched)
    carried = subprocess.run(["grep", "-E", pattern, str(tmp_path / "ssl.conf")],
                             capture_output=True, text=True).stdout.splitlines()
    stripped = [l.strip() for l in carried]
    assert "IncludeOptional /etc/apache2/conf-available/asl-web-ui.conf" in stripped
    assert "ProxyPass /asl-web-ui !" in stripped
    assert "ProxyPass /ws-audio ws://127.0.0.1:8098/ retry=0" in stripped
    # Nothing else: not HenWen's own catch-all, which the rewrite emits itself.
    assert not any(l.startswith("ProxyPass        /") for l in stripped)
    assert stripped.index("IncludeOptional /etc/apache2/conf-available/asl-web-ui.conf") < \
        stripped.index("ProxyPass /asl-web-ui !")


def test_setup_https_full_flow_carries_the_rules(tmp_path):
    """Review of PR #14: the full flow rewrites the port-80 vhost from
    scratch too. Run its own carry grep and re-insert on a scratch vhost."""
    src = open(os.path.join(ROOT, "tx-spike", "setup-https.sh")).read()
    pattern = re.search(r"ASL_CARRY=\$\(grep -E '([^']+)' \"\$HTTP_AVAIL\"", src).group(1)
    assert ('henwen_insert_before_proxypass_each_block "$HTTP_AVAIL" "$FLASK_PORT" '
            '"HenWen: ASL Web UI" "$ASL_CARRY"') in src
    _, patched = _patch(tmp_path)
    old = tmp_path / "old.conf"; old.write_text(patched)
    fresh = tmp_path / "fresh.conf"; fresh.write_text(VHOST)
    script = (f'. "{COMMON}"\nC=$(grep -E \'{pattern}\' "{old}")\n'
              f'henwen_insert_before_proxypass_each_block "{fresh}" {PORT} "HenWen: ASL Web UI" "$C"\n')
    assert subprocess.run(["bash", "-c", script]).returncode == 0
    lines = [l.strip() for l in fresh.read_text().splitlines()]
    assert lines.index("IncludeOptional /etc/apache2/conf-available/asl-web-ui.conf") \
        < lines.index("ProxyPass /asl-web-ui !") \
        < next(i for i, l in enumerate(lines) if l.startswith("ProxyPass        /"))


def test_rollback_reenables_only_what_apply_disabled():
    assert 'touch "$BACKUP_DIR/global-conf-was-enabled"' in SRC
    assert 'if [ -e "$BACKUP_DIR/global-conf-was-enabled" ]' in open(ROLLBACK).read()


APACHE = shutil.which("apache2") or "/usr/sbin/apache2"
MODULES = "/usr/lib/apache2/modules"


@pytest.mark.skipif(not (os.access(APACHE, os.X_OK) and os.path.isdir(MODULES)),
                    reason="apache2 not installed")
def test_patched_vhost_passes_configtest_before_the_package_exists(tmp_path):
    """IncludeOptional of a file that does not exist yet must not break Apache:
    the script is meant to be safe to run before asl-web-ui is installed."""
    rc, vhost = _patch(tmp_path, conf_available=str(tmp_path / "not-installed.conf"))
    assert rc == 0
    conf = tmp_path / "httpd.conf"
    conf.write_text(f"""ServerRoot {tmp_path}
Listen 127.0.0.1:18481
PidFile {tmp_path}/pid
ErrorLog {tmp_path}/err
LoadModule mpm_event_module {MODULES}/mod_mpm_event.so
LoadModule authz_core_module {MODULES}/mod_authz_core.so
LoadModule proxy_module {MODULES}/mod_proxy.so
LoadModule proxy_http_module {MODULES}/mod_proxy_http.so
{vhost.replace('*:443', '127.0.0.1:18481')}""")
    r = subprocess.run([APACHE, "-t", "-f", str(conf)], capture_output=True, text=True)
    assert "Syntax OK" in r.stderr + r.stdout, r.stderr


def test_generated_sip_files_never_show_up_as_asl_customizations():
    """Hazard 6: the UI's Nodes > Customizations scans custom/*.conf for
    ";MENU:" lines, and HenWen's generated files live in custom/. Without such
    a line they never appear there."""
    pjsip = app.render_pjsip_users([("N8GMZ", "s" * 32), ("K8DIY", "t" * 32)])
    plan = app.render_user_dialplans([("N8GMZ", "643930"), ("K8DIY", "643930")])
    assert ";MENU:" not in pjsip and ";MENU:" not in plan
