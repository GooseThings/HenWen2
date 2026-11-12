"""Runs the real shell/python snippets out of dvswitch/apply.sh (never the
whole script -- that installs packages and enables services as root, and
must not be executed by tests) to prove the latitude/longitude plumbing:
the JSON extraction emits DVS_LATITUDE/DVS_LONGITUDE, patch_ini writes them
into [Info] of a scratch copy of a realistic MMDVM_Bridge.ini, and the
script refuses to proceed without them. `bash -n` covers syntax separately.
"""
import json
import os
import re
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "dvswitch", "apply.sh")
SRC = open(SCRIPT).read()

# Same shape as the package-shipped ini: [Info] present with a stale location,
# plus lookalike keys in other sections that must not be touched.
INI = """[General]
Callsign=N8GMZ
Id=320601211

[Info]
RXFrequency=433800000
Latitude=41.7333
Longitude=-50.3999
Location=Spring Lake, Michigan

[DMR Network]
Enable = 0
Latitude = untouched
"""


def _func_body(name):
    m = re.search(rf'^{name}\(\) \{{\n.*?^\}}\n', SRC, re.S | re.M)
    assert m, f"{name}() not found in apply.sh"
    return m.group(0)


def _bash(script, **env):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={**os.environ, **env})


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", SCRIPT]).returncode == 0


def test_json_extraction_exports_location(tmp_path):
    py = re.search(r"eval \"\$\(python3 - \"\$CONFIG_JSON\" <<'PYEOF'\n(.*?)\nPYEOF", SRC, re.S).group(1)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"dmr_id": "3123456", "latitude": 43.0731, "longitude": -86.2012}))
    out = subprocess.run(["python3", "-", str(cfg)], input=py, capture_output=True, text=True).stdout
    assert "DVS_LATITUDE=43.0731" in out and "DVS_LONGITUDE=-86.2012" in out


def test_patch_ini_updates_only_the_info_section(tmp_path):
    ini = tmp_path / "MMDVM_Bridge.ini"
    ini.write_text(INI)
    r = _bash(f'{_func_body("patch_ini")}\n'
              f'patch_ini "$INI" "Info" "Latitude" "43.0731"\n'
              f'patch_ini "$INI" "Info" "Longitude" "-86.2012"', INI=str(ini))
    assert r.returncode == 0, r.stderr
    text = ini.read_text()
    assert "Latitude = 43.0731" in text and "Longitude = -86.2012" in text
    assert "41.7333" not in text and "-50.3999" not in text
    assert "Latitude = untouched" in text                 # other section left alone
    assert "Location=Spring Lake, Michigan" in text        # neighbouring keys preserved
    assert text.count("Latitude") == 2                     # replaced, not duplicated


def test_patch_ini_adds_keys_when_absent(tmp_path):
    ini = tmp_path / "m.ini"
    ini.write_text("[Info]\nRXFrequency=1\n\n[Other]\nx=1\n")
    _bash(f'{_func_body("patch_ini")}\npatch_ini "$INI" "Info" "Latitude" "1.5"', INI=str(ini))
    assert ini.read_text().index("Latitude = 1.5") < ini.read_text().index("[Other]")


def test_script_refuses_without_location(tmp_path):
    guard = re.search(r'(\[ -n "\$DVS_LATITUDE" \].*?\n\})', SRC, re.S).group(1)
    r = _bash(f'CONFIG_JSON=/x/c.json\n{guard}', DVS_LATITUDE="", DVS_LONGITUDE="")
    assert r.returncode == 1 and "latitude/longitude missing" in r.stdout
    ok = _bash(f'CONFIG_JSON=/x/c.json\n{guard}\necho proceeded', DVS_LATITUDE="43", DVS_LONGITUDE="-86")
    assert ok.returncode == 0 and "proceeded" in ok.stdout


LOGROTATE_RULE = """/var/log/mmdvm/MMDVM_Bridge.log
{
        daily
        postrotate
                systemctl reload mmdvm_bridge
        endscript
}
"""


def _guard(tmp_path, name, unit):
    return _bash(f'LOGROTATE_DIR="{tmp_path}"\n{_func_body("guard_logrotate_reload")}\n'
                 f'guard_logrotate_reload {name} {unit}')


def test_logrotate_guard_only_reloads_an_active_unit(tmp_path):
    rule = tmp_path / "MMDVM_Bridge"
    rule.write_text(LOGROTATE_RULE)
    assert _guard(tmp_path, "MMDVM_Bridge", "mmdvm_bridge").returncode == 0
    out = rule.read_text()
    assert ("                systemctl is-active --quiet mmdvm_bridge && "
            "systemctl reload mmdvm_bridge || true\n") in out
    assert "\n                systemctl reload mmdvm_bridge\n" not in out


def test_logrotate_guard_is_idempotent_and_scoped(tmp_path):
    rule = tmp_path / "MMDVM_Bridge"
    rule.write_text(LOGROTATE_RULE)
    _guard(tmp_path, "MMDVM_Bridge", "mmdvm_bridge")
    once = rule.read_text()
    _guard(tmp_path, "MMDVM_Bridge", "mmdvm_bridge")
    assert rule.read_text() == once
    # A different unit's reload line is not touched.
    other = tmp_path / "Analog_Bridge"
    other.write_text(LOGROTATE_RULE.replace("mmdvm_bridge", "analog_bridge"))
    before = other.read_text()
    _guard(tmp_path, "Analog_Bridge", "mmdvm_bridge")
    assert other.read_text() == before


def test_logrotate_guard_skips_a_missing_rule(tmp_path):
    assert _guard(tmp_path, "Analog_Reflector", "analog_reflector").returncode == 0
    assert not (tmp_path / "Analog_Reflector").exists()
