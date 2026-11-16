"""Courtesy tone for browser transmissions (issue #43). app_rpt plays none
for a phone-mode link's '#' unkey, so HenWen sends `rpt localplay` itself.
Nothing here touches Asterisk: the timer, the play and rpt.conf are stubbed."""
import os
import struct

import pytest

import app

SIP_CH = "PJSIP/henwen-u-n8gmz-0000001a"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    app._sip_keyed_reset()
    app._armtx_keyed_reset()
    app._ami_cache.clear()
    yield
    with app._ct_lock:
        for t in app._ct_timers.values():
            t.cancel()
        app._ct_timers.clear()
    app._sip_keyed_reset()
    app._armtx_keyed_reset()
    app._ami_cache.clear()


def sip_dtmf(digit, uid="1.1", exten="643930"):
    return {"Event": "DTMFEnd", "Channel": SIP_CH, "Context": "henwen-u-n8gmz",
            "Uniqueid": uid, "Exten": exten, "Digit": digit, "Direction": "Received"}


def armtx_dtmf(digit, uid="2.1", exten="2643930", direction="Received"):
    return {"Event": "DTMFEnd", "Channel": "PJSIP/henwen-tx-00000003", "Context": "henwen-browser-tx",
            "Uniqueid": uid, "Exten": exten, "Digit": digit, "Direction": direction}


# ── Tone specs and synthesis ─────────────────────────────────────────────────

class TestParseToneSpec:
    def test_single_and_multi_segment(self):
        assert app.parse_tone_spec("|t(700,1100,150,2048)") == [(700, 1100, 150, 2048)]
        assert app.parse_tone_spec("|t(1633,0,50,1000)(0,0,30,0)(1209,0,50,1000)") == [
            (1633, 0, 50, 1000), (0, 0, 30, 0), (1209, 0, 50, 1000)]

    @pytest.mark.parametrize("spec", [
        "rpt/callproceeding",          # a sound file, not a tone
        "", None, "|t()", "|t(700,1100)",
        "|t(700,0,0,2048)",            # zero length
        "|t(700,0,9000,2048)",         # absurdly long
        "|t(9000,0,100,2048)",         # above Nyquist at 8 kHz
    ])
    def test_rejects(self, spec):
        assert app.parse_tone_spec(spec) is None


class TestTonePcm:
    def test_length_and_peak(self):
        pcm = app.tone_pcm([(700, 1100, 150, 2048), (0, 0, 30, 0)])
        assert len(pcm) == 2 * int(8000 * 0.180)
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        assert max(abs(s) for s in samples) <= 4096           # two tones at 2048 peak each
        assert max(abs(s) for s in samples[:1200]) > 1000      # audible
        assert all(s == 0 for s in samples[-240:])             # the silent gap

    def test_ramped_edges(self):
        samples = struct.unpack("<800h", app.tone_pcm([(1000, 0, 100, 2048)]))
        assert samples[0] == 0 and abs(samples[1]) < 200 and abs(samples[-1]) < 200


# ── Resolving what to play ───────────────────────────────────────────────────

TELEM = {"ct2": "|t(660,880,150,2048)", "ct8": "|t(700,1100,150,2048)", "ct9": "rpt/somefile"}


class TestResolve:
    def test_node_uses_its_linkunkeyct(self):
        assert app.resolve_courtesy_tone("node", {"duplex": "2", "linkunkeyct": "ct8"}, TELEM) == (
            "tone", [(700, 1100, 150, 2048)])

    @pytest.mark.parametrize("ns", [
        {"duplex": "2"},                              # no linkunkeyct
        {"duplex": "2", "linkunkeyct": "ct9"},       # a sound file
        {"duplex": "2", "linkunkeyct": "ct99"},      # not defined
    ])
    def test_node_falls_back_to_beep(self, ns):
        assert app.resolve_courtesy_tone("node", ns, TELEM) == (
            "tone", app.COURTESY_TONE_CHOICES["beep"][1])

    def test_presets(self):
        assert app.resolve_courtesy_tone("chirp-up", {"linkunkeyct": "ct8"}, TELEM) == (
            "tone", app.COURTESY_TONE_CHOICES["chirp-up"][1])

    def test_app_rpt_gates(self):
        assert app.resolve_courtesy_tone("node", {"duplex": "0", "linkunkeyct": "ct8"}, TELEM)[0] == "skip"
        assert app.resolve_courtesy_tone("beep", {"nounkeyct": "1"}, TELEM)[0] == "skip"
        assert app.resolve_courtesy_tone("beep", {"nounkeyct": "0"}, TELEM)[0] == "tone"


# ── Arm TX tracking (kept out of the session table) ─────────────────────────

class TestArmTx:
    def test_key_and_unkey(self):
        assert app._armtx_keyed_handle_event(armtx_dtmf("*")) is None
        assert app._armtx_keyed_handle_event(armtx_dtmf("9")) is None
        assert app._armtx_keyed_handle_event(armtx_dtmf("9")) == ("key", "643930")
        assert app._armtx_keyed_handle_event(armtx_dtmf("#")) == ("unkey", "643930")
        assert app._armtx_keyed_handle_event(armtx_dtmf("#")) is None

    def test_not_in_session_table(self):
        for d in "*99":
            app._armtx_keyed_handle_event(armtx_dtmf(d))
        assert app._sip_tracked_callsigns() == set()

    def test_hangup_forgets_without_transition(self):
        for d in "*99":
            app._armtx_keyed_handle_event(armtx_dtmf(d))
        assert app._armtx_keyed_handle_event({"Event": "Hangup", "Uniqueid": "2.1"}) is None
        assert not app._ct_channel_busy("643930")

    @pytest.mark.parametrize("ev", [
        armtx_dtmf("9", direction="Sent"),
        dict(armtx_dtmf("9"), Context="radio-secure"),
        armtx_dtmf("9", exten="s"),
    ])
    def test_ignored(self, ev):
        assert app._armtx_keyed_handle_event(ev) is None


# ── Dispatch: when a tone is scheduled ───────────────────────────────────────

@pytest.fixture
def scheduled(monkeypatch):
    """Record courtesy_tone_unkey/key calls instead of starting timers."""
    calls = []
    monkeypatch.setattr(app, "courtesy_tone_unkey", lambda node, who="": calls.append(("unkey", node, who)))
    monkeypatch.setattr(app, "courtesy_tone_key", lambda node: calls.append(("key", node)))
    return calls


class TestDispatch:
    def test_session_hash_unkey_schedules(self, scheduled):
        for d in "*99#":
            app._sip_keyed_dispatch(sip_dtmf(d))
        assert scheduled == [("key", "643930"), ("unkey", "643930", "N8GMZ")]

    def test_session_hangup_while_keyed_does_not(self, scheduled):
        for d in "*99":
            app._sip_keyed_dispatch(sip_dtmf(d))
        app._sip_keyed_dispatch({"Event": "Hangup", "Uniqueid": "1.1", "Channel": SIP_CH})
        assert scheduled == [("key", "643930")]

    def test_armtx_unkey_schedules(self, scheduled):
        for d in "*99#":
            app._sip_keyed_dispatch(armtx_dtmf(d))
        assert scheduled == [("key", "643930"), ("unkey", "643930", "Arm TX")]


class _FakeTimer:
    made = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args, self.cancelled, self.daemon = delay, fn, args, False, False
        _FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class TestTimers:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        _FakeTimer.made = []
        monkeypatch.setattr(app.threading, "Timer", _FakeTimer)
        monkeypatch.setattr(app, "courtesy_tone_config", lambda: {"enabled": True, "tone": "beep"})

    def test_rekey_cancels_pending(self):
        app.courtesy_tone_unkey("643930", "N8GMZ")
        t = _FakeTimer.made[-1]
        assert t.delay == app.COURTESY_TONE_DEBOUNCE_SEC and t.args == ("643930", "N8GMZ")
        app.courtesy_tone_key("643930")
        assert t.cancelled

    def test_second_unkey_replaces_first(self):
        app.courtesy_tone_unkey("643930")
        app.courtesy_tone_unkey("643930")
        assert _FakeTimer.made[0].cancelled and not _FakeTimer.made[1].cancelled

    def test_disabled_schedules_nothing(self, monkeypatch):
        monkeypatch.setattr(app, "courtesy_tone_config", lambda: {"enabled": False, "tone": "beep"})
        app.courtesy_tone_unkey("643930")
        assert _FakeTimer.made == []


class TestFire:
    @pytest.fixture
    def played(self, monkeypatch):
        calls = []
        monkeypatch.setattr(app, "courtesy_tone_config", lambda: {"enabled": True, "tone": "beep"})
        monkeypatch.setattr(app, "play_courtesy_tone", lambda node, tone=None: calls.append(node) or (True, "x"))
        return calls

    def test_plays_when_idle(self, played):
        app._courtesy_tone_fire("643930", "N8GMZ")
        assert played == ["643930"]

    def test_skips_when_receiver_keyed(self, played):
        app._ami_cache["643930"] = {"keyed": True, "links": {}}
        app._courtesy_tone_fire("643930")
        assert played == []

    def test_skips_when_a_node_link_is_keyed(self, played):
        app._ami_cache["643930"] = {"keyed": False, "links": {"27339": {"keyed": True}}}
        app._courtesy_tone_fire("643930")
        assert played == []

    def test_stale_overlay_of_the_unkeyed_session_does_not_block(self, played, monkeypatch):
        """The poll cache can still show the session that just unkeyed."""
        monkeypatch.setattr(app, "_sip_session_link", lambda name: name == "N8GMZ")
        app._ami_cache["643930"] = {"keyed": False, "links": {"N8GMZ": {"keyed": True, "mode": "P"}}}
        app._courtesy_tone_fire("643930")
        assert played == ["643930"]

    def test_skips_while_another_session_is_keyed(self, played):
        for d in "*99":
            app._sip_keyed_handle_event(sip_dtmf(d, uid="9.9"))
        app._courtesy_tone_fire("643930")
        assert played == []


class TestPlay:
    def test_localplay_of_generated_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "SOUNDS_DIR", str(tmp_path))
        monkeypatch.setattr(app, "_ct_node_settings", lambda n: ({"duplex": "2", "linkunkeyct": "ct8"}, TELEM))
        monkeypatch.setattr(app, "_convert_to_ulaw", lambda src, dest: open(dest, "wb").write(b"\xff" * 10))
        sent = []

        class FakeAmi:
            def command(self, c):
                sent.append(c)
                return []
        monkeypatch.setattr(app, "ami_send_command", lambda fn: fn(FakeAmi()))
        played, path = app.play_courtesy_tone("643930", "node")
        slug = app._ct_sound_slug([(700, 1100, 150, 2048)])
        assert played and path == f"{app.SOUNDS_SUBDIR}/{slug}"
        assert sent == [f"rpt localplay 643930 {app.SOUNDS_SUBDIR}/{slug}"]
        assert os.listdir(tmp_path) == [f"{slug}.ulaw"]

    def test_gate_skips_without_playing(self, monkeypatch):
        monkeypatch.setattr(app, "_ct_node_settings", lambda n: ({"duplex": "0"}, {}))
        monkeypatch.setattr(app, "ami_send_command", lambda fn: pytest.fail("must not play"))
        assert app.play_courtesy_tone("643930", "beep")[0] is False


# ── Routes ───────────────────────────────────────────────────────────────────

def _login(client, username, password="password12345"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302


class TestRoutes:
    def test_default_off_and_save(self, client, create_user):
        create_user("adm", role="admin")
        create_user("own", role="owner")
        _login(client, "adm")
        d = client.get("/api/courtesy-tone").get_json()
        assert d["enabled"] is False and d["tone"] == "node"
        assert {c["value"] for c in d["choices"]} == set(app.COURTESY_TONE_CHOICES)
        r = client.post("/api/courtesy-tone", json={"enabled": True, "tone": "chirp-up"})
        assert r.status_code == 200 and r.get_json() == {"enabled": True, "tone": "chirp-up"}
        assert client.post("/api/courtesy-tone", json={"tone": "kazoo"}).status_code == 400

    def test_user_role_refused(self, client, create_user):
        create_user("own", role="owner")
        create_user("u", role="user")
        _login(client, "u")
        assert client.get("/api/courtesy-tone").status_code == 403
        assert client.post("/api/courtesy-tone/test", json={"node": "643930"}).status_code == 403

    def test_test_route_requires_local_node(self, client, create_user, monkeypatch):
        create_user("own", role="owner")
        _login(client, "own")
        monkeypatch.setattr(app, "read_conf_file", lambda p: "")
        monkeypatch.setattr(app, "get_node_numbers", lambda c: ["643930"])
        assert client.post("/api/courtesy-tone/test", json={"node": "27339"}).status_code == 400
        monkeypatch.setattr(app, "play_courtesy_tone", lambda n, t=None: (True, "x"))
        assert client.post("/api/courtesy-tone/test", json={"node": "643930"}).get_json() == {"ok": True}


# ── rpt.conf: values as Asterisk reads them ──────────────────────────────────

CONF = """
[node-main](!)
duplex = 2                 ; full duplex
linkunkeyct = ct8          ; sent when a link unkeys
telemetry = telemetry

[telemetry-main](!)
; |m - Morse escape sequence
; Sends Morse code at the telemetry amplitude, as defined in the
; [morse] section.
ct8 = |t(700,1100,150,2048)

[telemetry](telemetry-main)

[morse](morse-main)

[643930](node-main,allscan-uci)
duplex = 3
;linkunkeyct = ct8
;nounkeyct = 1
"""


class TestRptConf:
    def test_comment_prose_is_not_a_header(self):
        """'; [morse] section.' used to end [telemetry-main] and hide every ctN."""
        st = app._collect_stanzas(CONF)
        assert any("ct8" in l for l in st["telemetry-main"]["lines"])

    def test_commented_headers_still_bound_stanzas(self):
        st = app._collect_stanzas("[macro]\n1=*81\n;[daq-cham-1]\n;device=x\n")
        assert st["macro"]["lines"] == ["1=*81"] and st["daq-cham-1"]["commented"]

    def test_effective_values_ignore_commented_overrides(self):
        st = app._collect_stanzas(CONF)
        ns = app.rpt_effective_values(st, "643930")
        assert ns["linkunkeyct"] == "ct8" and ns["duplex"] == "3" and "nounkeyct" not in ns
        ts = app.rpt_effective_values(st, ns["telemetry"])
        assert app.resolve_courtesy_tone("node", ns, ts) == ("tone", [(700, 1100, 150, 2048)])

    def test_template_cycle_is_safe(self):
        st = app._collect_stanzas("[a](b)\nx=1\n[b](a)\ny=2\n")
        assert app.rpt_effective_values(st, "a") == {"y": "2", "x": "1"}

    def test_arrow_assignment(self):
        st = app._collect_stanzas("[n]\nlinkunkeyct => ct8 ; tone\nduplex=>2\n")
        assert app.rpt_effective_values(st, "n") == {"linkunkeyct": "ct8", "duplex": "2"}

    def test_save_path_agrees_on_boundaries(self):
        """update_setting_in_content() must not treat comment prose as a header."""
        assert app._section_header_match("; [morse] section.") is None
        assert app._section_header_match(";[daq-cham-1]").group(1) == "daq-cham-1"
        assert app._section_header_match("[643930](node-main,allscan-uci) ; x").group(1) == "643930"


class TestSoundFiles:
    def _gen(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "SOUNDS_DIR", str(tmp_path))
        monkeypatch.setattr(app, "_convert_to_ulaw", lambda src, dest: open(dest, "wb").write(b"\xff"))

    def test_recent_other_tone_survives(self, monkeypatch, tmp_path):
        """app_rpt opens a queued tone only after telemwait; don't delete it."""
        self._gen(monkeypatch, tmp_path)
        a = app._ct_ensure_sound([(1000, 0, 100, 2048)])
        app._ct_ensure_sound([(700, 1100, 150, 2048)])
        assert os.path.exists(os.path.join(str(tmp_path), a.split("/")[-1] + ".ulaw"))

    def test_stale_tone_pruned(self, monkeypatch, tmp_path):
        self._gen(monkeypatch, tmp_path)
        old = tmp_path / "_courtesy_tone_deadbeef.ulaw"
        old.write_bytes(b"x")
        os.utime(old, (1, 1))
        app._ct_ensure_sound([(700, 1100, 150, 2048)])
        assert not old.exists()

    def test_hidden_from_sound_picker(self, monkeypatch, tmp_path):
        d = tmp_path / "henwen"
        d.mkdir()
        for n in ("_courtesy_tone_abc.ulaw", "_tts_preview.ulaw", "club-id.ulaw"):
            (d / n).write_bytes(b"x")
        monkeypatch.setattr(app, "ASTERISK_SOUNDS_ROOT", str(tmp_path))
        monkeypatch.setattr(app, "_packaged_sound_files", lambda: set())
        assert [s["path"] for s in app._list_asterisk_sounds()] == ["henwen/club-id"]
