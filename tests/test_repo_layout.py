"""The sudoers rule, app.py's script paths and the files on disk must agree.

sudo matches the literal path it is given, so a script that moves without
its rule (or a rule that names a path app.py doesn't use) fails only on a
live box, as "sudo: a password is required". These tie the three together.
"""
import os
import re
import subprocess

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVISION = os.path.join(ROOT, "scripts", "provision-sudoers.sh")


def _sudoers_paths():
    """Every ${INSTALL_DIR}/... path in provision-sudoers.sh's rule, resolved
    against this checkout."""
    text = open(PROVISION).read()
    rels = re.findall(r"\$\{INSTALL_DIR\}/(\S+)", text)
    return {os.path.join(ROOT, r) for r in rels}


def test_every_sudoers_path_exists_and_is_executable():
    paths = _sudoers_paths()
    assert len(paths) == 9
    for p in paths:
        assert os.path.isfile(p), p
        assert os.access(p, os.X_OK), p


def test_app_invokes_exactly_the_sudoers_paths():
    # Each script app.py runs through `sudo -n` (or systemd-run) by path.
    via_sudo = {
        app.UPDATE_SCRIPT_PATH,
        app.ASL_UPDATE_SCRIPT_PATH,
        app.ROTATE_SECRET_KEY_SCRIPT_PATH,
        app.UPDATE_SERVICE_PORTS_SCRIPT_PATH,
        app.AUDIOSOCKET_TAP_APPLY_SCRIPT_PATH,
        app.WS_AUDIO_APPLY_SCRIPT_PATH,
        app.TX_APPLY_SCRIPT_PATH,
        app.DVSWITCH_APPLY_SCRIPT_PATH,
        app.SIP_USERS_APPLY_SCRIPT_PATH,
    }
    assert {os.path.realpath(p) for p in via_sudo} == \
        {os.path.realpath(p) for p in _sudoers_paths()}


def test_other_paths_app_uses_exist():
    for p in (app.TX_CHECK_PORTS_SCRIPT, app.DVSWITCH_CHECK_SCRIPT_PATH,
              app.ASL_WEB_UI_APPLY_SCRIPT, app._AUDIO_RELAY_SCRIPT,
              app._AUDIO_WS_RELAY_SCRIPT):
        assert os.path.isfile(p), p


def test_every_script_parses_and_its_sources_resolve():
    scripts = []
    for d in ("scripts", "setup"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, d)):
            scripts += [os.path.join(dirpath, f) for f in files if f.endswith(".sh")]
    assert scripts
    checked = []
    for s in scripts:
        r = subprocess.run(["bash", "-n", s], capture_output=True, text=True)
        assert r.returncode == 0, (s, r.stderr)
        here = os.path.dirname(s)
        sources = re.findall(
            r'^\s*\. "(?:\$(?:HERE|SPIKE_DIR|SCRIPT_DIR)|\$\(cd "\$\(dirname "\$0"\)" && pwd\))/([^"]+)"',
            open(s).read(), re.M)
        for rel in sources:
            assert os.path.isfile(os.path.join(here, rel)), (s, rel)
            checked.append(s)
    # Every script under setup/ that loads apache-common.sh was seen above
    # (install.sh loads it via $INSTALL_DIR, the install target, not itself).
    setup_dir = os.path.join(ROOT, "setup") + os.sep
    common_users = [s for s in scripts if s.startswith(setup_dir) and "apache-common.sh" in
                    "".join(l for l in open(s) if l.lstrip().startswith(". "))]
    assert common_users
    assert sorted(set(c for c in checked if c.startswith(setup_dir))) == sorted(common_users)
