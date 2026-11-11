"""Session keyed state from AMI DTMF (see the block comment above
_SIP_CHANNEL_RE in app.py): app_rpt never sets a phone link's K flag, so a
listener's *99 / # are followed from DTMFEnd events instead."""
import socket
import threading

import pytest

import app as henwen_app

CH = "PJSIP/henwen-u-n8gmz-0000001a"


@pytest.fixture(autouse=True)
def _clean_table():
    henwen_app._sip_keyed_reset()
    yield
    henwen_app._sip_keyed_reset()


def dtmf(digit, channel=CH, uid="1700000000.1", exten="643930", direction="Received"):
    return {"Event": "DTMFEnd", "Channel": channel, "Uniqueid": uid,
            "Exten": exten, "Digit": digit, "Direction": direction}


def press(*digits, **kw):
    out = None
    for d in digits:
        t = henwen_app._sip_keyed_handle_event(dtmf(d, **kw), now=100.0)
        out = t or out
    return out


class TestChannelCallsign:
    def test_session_channel(self):
        assert henwen_app._sip_channel_callsign(CH) == "N8GMZ"

    @pytest.mark.parametrize("ch", [
        "PJSIP/henwen-tx-00000003",          # shared 1.x Arm-TX endpoint
        "IAX2/27339-1234",
        "PJSIP/someone-else-0000001a",
        "Local/643930@henwen-u-n8gmz-0000;1",
        "", None,
    ])
    def test_not_a_session(self, ch):
        assert henwen_app._sip_channel_callsign(ch) is None


class TestEventCallsign:
    def test_context_wins(self):
        """Verified live: a Local/ call into the account's context reports it."""
        ev = {"Channel": "Local/643930@henwen-u-n8gmz-00000072;2", "Context": "henwen-u-n8gmz"}
        assert henwen_app._sip_event_callsign(ev) == "N8GMZ"

    def test_channel_fallback(self):
        assert henwen_app._sip_event_callsign({"Channel": CH}) == "N8GMZ"

    def test_neither(self):
        assert henwen_app._sip_event_callsign(
            {"Channel": "IAX2/27339-1", "Context": "radio-secure"}) is None

    def test_hangup_request_is_not_a_hangup(self):
        """The 'Event: Hangup' filter is a substring match, so HangupRequest
        arrives too; it must not end a keyed session early."""
        press("*", "9", "9")
        assert henwen_app._sip_keyed_handle_event(
            {"Event": "HangupRequest", "Channel": CH, "Uniqueid": "1700000000.1"}) is None
        assert henwen_app._sip_session_keyed("643930", "N8GMZ")


class TestKeyedTransitions:
    def test_star99_keys_and_hash_unkeys(self):
        assert press("*", "9", "9") == ("key", "N8GMZ", "643930", None)
        assert henwen_app._sip_session_keyed("643930", "N8GMZ")
        assert henwen_app._sip_session_keyed("643930", "n8gmz")
        t = henwen_app._sip_keyed_handle_event(dtmf("#"), now=104.5)
        assert t == ("unkey", "N8GMZ", "643930", 4.5)
        assert not henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_other_digits_do_not_key(self):
        assert press("*", "9", "8") is None
        assert press("9", "9") is None          # no leading *
        assert not henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_hash_while_unkeyed_is_quiet(self):
        assert henwen_app._sip_keyed_handle_event(dtmf("#")) is None

    def test_repeat_star99_while_keyed_is_not_a_new_key(self):
        press("*", "9", "9")
        assert press("*", "9", "9") is None
        assert henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_sent_digits_are_ignored(self):
        assert press("*", "9", "9", direction="Sent") is None
        assert not henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_non_session_channel_is_ignored(self):
        assert press("*", "9", "9", channel="IAX2/27339-1234") is None
        assert henwen_app._sip_tracked_callsigns() == set()

    def test_hangup_while_keyed_unkeys(self):
        press("*", "9", "9")
        t = henwen_app._sip_keyed_handle_event(
            {"Event": "Hangup", "Channel": CH, "Uniqueid": "1700000000.1"}, now=130.0)
        assert t == ("unkey", "N8GMZ", "643930", 30.0)
        assert henwen_app._sip_tracked_callsigns() == set()

    def test_hangup_of_unrelated_channel_is_quiet(self):
        press("*", "9", "9")
        assert henwen_app._sip_keyed_handle_event(
            {"Event": "Hangup", "Channel": "IAX2/x", "Uniqueid": "9"}) is None
        assert henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_keyed_is_per_node(self):
        press("*", "9", "9")
        assert not henwen_app._sip_session_keyed("27339", "N8GMZ")

    def test_unknown_node_counts_for_any_node(self):
        press("*", "9", "9", exten="s")
        assert henwen_app._sip_session_keyed("643930", "N8GMZ")

    def test_two_sessions_are_independent(self):
        press("*", "9", "9")
        press("*", "9", "9", channel="PJSIP/henwen-u-k8diy-0000001b", uid="2")
        henwen_app._sip_keyed_handle_event(dtmf("#"))
        assert not henwen_app._sip_session_keyed("643930", "N8GMZ")
        assert henwen_app._sip_session_keyed("643930", "K8DIY")

    def test_reset_clears(self):
        press("*", "9", "9")
        henwen_app._sip_keyed_reset("test")
        assert not henwen_app._sip_session_keyed("643930", "N8GMZ")


class TestOverlay:
    def status(self):
        return {"connected": ["27339", "N8GMZ"],
                "links": {"27339": {"keyed": False, "mode": "T"},
                          "N8GMZ": {"keyed": False, "mode": "P"}}}

    def test_keyed_session_marks_its_link(self):
        press("*", "9", "9")
        st = henwen_app._sip_overlay_keyed("643930", self.status())
        assert st["links"]["N8GMZ"]["keyed"] is True
        assert st["links"]["27339"]["keyed"] is False

    def test_unkeyed_session_left_alone(self):
        st = henwen_app._sip_overlay_keyed("643930", self.status())
        assert st["links"]["N8GMZ"]["keyed"] is False

    def test_missing_alinks_entry_is_created(self):
        press("*", "9", "9")
        st = {"connected": ["N8GMZ"], "links": {}}
        henwen_app._sip_overlay_keyed("643930", st)
        assert st["links"]["N8GMZ"] == {"keyed": True, "mode": "P"}

    def test_session_not_in_lstats_adds_nothing(self):
        press("*", "9", "9")
        st = henwen_app._sip_overlay_keyed("643930", {"connected": ["27339"], "links": {}})
        assert st["links"] == {}

    def test_overlay_never_reads_the_database(self, monkeypatch):
        """It runs under _ami_pool_lock; DB work there stalls every AMI user."""
        def boom(*a, **k):
            raise AssertionError("DB read under the AMI lock")
        monkeypatch.setattr(henwen_app, "get_db", boom)
        monkeypatch.setattr(henwen_app, "_sip_session_callsigns", boom)
        press("*", "9", "9")
        henwen_app._sip_overlay_keyed("643930", self.status())


class TestPacketSplit:
    def test_complete_and_partial(self):
        buf = ("Event: DTMFEnd\r\nChannel: " + CH + "\r\nDigit: *\r\n\r\n"
               "Response: Success\r\nPing: Pong\r\n\r\nEvent: Hang")
        pkts, rest = henwen_app._ami_split_packets(buf)
        assert pkts[0]["Event"] == "DTMFEnd" and pkts[0]["Digit"] == "*"
        assert pkts[1]["Ping"] == "Pong"
        assert rest == "Event: Hang"


class TestEventLoop:
    def test_reads_events_from_a_live_socket(self, monkeypatch):
        """End to end against a fake AMI: login with an event mask, filters,
        then *99 / # on a session channel."""
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        seen, stop, keyed_seen = [], threading.Event(), threading.Event()

        def fake_ami():
            conn, _ = srv.accept()
            conn.sendall(b"Asterisk Call Manager/9.0.0\r\n")
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            seen.append(data.decode())
            conn.sendall(b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n")
            for d in "*99":
                conn.sendall(("Event: DTMFEnd\r\nChannel: %s\r\nUniqueid: 1\r\n"
                              "Exten: 643930\r\nDigit: %s\r\nDirection: Received\r\n\r\n"
                              % (CH, d)).encode())
            for _ in range(50):
                if henwen_app._sip_session_keyed("643930", "N8GMZ"):
                    keyed_seen.set(); break
                threading.Event().wait(0.05)
            conn.sendall(("Event: DTMFEnd\r\nChannel: %s\r\nUniqueid: 1\r\n"
                          "Digit: #\r\nDirection: Received\r\n\r\n" % CH).encode())
            threading.Event().wait(0.3)
            seen.append(henwen_app._sip_session_keyed("643930", "N8GMZ"))
            stop.set()
            conn.close()

        monkeypatch.setattr(henwen_app, "_sip_session_callsigns", lambda: frozenset({"N8GMZ"}))
        monkeypatch.setattr(henwen_app, "parse_manager_conf",
                            lambda: {"host": "127.0.0.1", "port": port, "user": "u", "secret": "s"})
        monkeypatch.setattr(henwen_app, "SIP_KEYED_RETRY_SEC", 0)
        t = threading.Thread(target=fake_ami, daemon=True); t.start()
        henwen_app._sip_keyed_event_loop(stop=stop)
        srv.close()
        assert "Events: dtmf,call" in seen[0]
        assert keyed_seen.is_set()
        assert seen[-1] is False


class TestEventLoopBackoff:
    def test_down_logs_once_and_backs_off(self, monkeypatch):
        logs, sleeps, stop = [], [], threading.Event()

        def fail():
            raise ConnectionRefusedError("refused")

        def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) >= 6:
                stop.set()

        monkeypatch.setattr(henwen_app, "_sip_session_callsigns", lambda: frozenset({"N8GMZ"}))
        monkeypatch.setattr(henwen_app, "parse_manager_conf", fail)
        monkeypatch.setattr(henwen_app.time, "sleep", fake_sleep)
        monkeypatch.setattr(henwen_app, "log", lambda lvl, msg: logs.append((lvl, msg)))
        henwen_app._sip_keyed_event_loop(stop=stop)
        assert [l for l in logs if l[0] == "WARN"] == [
            ("WARN", "[SIP-KEY] Event stream down: refused (retrying quietly)")]
        assert sleeps[:6] == [5, 10, 20, 40, 80, 120]


class TestLoginLeftover:
    def test_events_in_the_login_read_reach_the_reader(self, monkeypatch):
        """PR #10 review: events that share a read with the Login reply,
        including a partial one, must not be dropped."""
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        stop, result = threading.Event(), []

        def ev(d):
            return ("Event: DTMFEnd\r\nChannel: %s\r\nUniqueid: 1\r\nExten: 643930\r\n"
                    "Digit: %s\r\nDirection: Received\r\n\r\n" % (CH, d))

        def fake_ami():
            conn, _ = srv.accept()
            conn.sendall(b"Asterisk Call Manager/9.0.0\r\n")
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            whole = "Response: Success\r\nMessage: Authentication accepted\r\n\r\n" + ev("*") + ev("9")
            last = ev("9")
            conn.sendall((whole + last[:20]).encode())     # reply + 2 events + partial 3rd, one write
            threading.Event().wait(0.3)
            conn.sendall(last[20:].encode())
            for _ in range(40):
                if henwen_app._sip_session_keyed("643930", "N8GMZ"):
                    break
                threading.Event().wait(0.05)
            result.append(henwen_app._sip_session_keyed("643930", "N8GMZ"))
            stop.set()
            conn.close()

        monkeypatch.setattr(henwen_app, "_sip_session_callsigns", lambda: frozenset({"N8GMZ"}))
        monkeypatch.setattr(henwen_app, "parse_manager_conf",
                            lambda: {"host": "127.0.0.1", "port": port, "user": "u", "secret": "s"})
        monkeypatch.setattr(henwen_app, "SIP_KEYED_RETRY_SEC", 0)
        threading.Thread(target=fake_ami, daemon=True).start()
        henwen_app._sip_keyed_event_loop(stop=stop)
        srv.close()
        assert result == [True]

    def test_command_connection_leftover_is_empty_by_default(self):
        assert henwen_app.AMIClient("h", 1, "u", "s").login_leftover == ""
