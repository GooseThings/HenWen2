"""TX Diagnostics' SIP WebSocket relay check (app.py)."""
import socket

import app


def _vhost(tmp_path, name, ws_port):
    p = tmp_path / name
    p.write_text(
        "<VirtualHost *:443>\n"
        "    ServerName a.example.com\n"
        f"    ProxyPass /asterisk-ws ws://127.0.0.1:{ws_port}/ws retry=0\n"
        f"    ProxyPass / http://127.0.0.1:{app.PORT}/ retry=0\n"
        "</VirtualHost>\n")
    return str(p)


def test_proxy_ports_found_per_vhost(tmp_path):
    a = _vhost(tmp_path, "a.conf", 8087)
    b = _vhost(tmp_path, "b.conf", 8088)
    none = tmp_path / "none.conf"
    none.write_text(f"ProxyPass / http://127.0.0.1:{app.PORT}/\n")
    missing = str(tmp_path / "missing.conf")
    assert app._asterisk_ws_proxy_ports([a, b, str(none), missing]) == {a: [8087], b: [8088]}


def test_check_passes_when_every_vhost_uses_running_relay():
    ok, warn, _ = app._sip_ws_relay_check({"a": [8087], "b": [8087]}, 8087, True)
    assert ok and not warn


def test_check_fails_when_relay_is_down():
    ok, warn, detail = app._sip_ws_relay_check({"a": [8087]}, 8087, False)
    assert not ok and not warn and "henwen-sip-ws" in detail


def test_check_warns_on_direct_proxy():
    ok, warn, detail = app._sip_ws_relay_check({"a": [8088]}, 8087, True)
    assert not ok and warn and "8088" in detail
    ok, warn, _ = app._sip_ws_relay_check({"a": [8087], "b": [8088]}, 8087, True)
    assert not ok and warn


def test_check_fails_without_proxy_line():
    ok, warn, _ = app._sip_ws_relay_check({}, 8087, True)
    assert not ok and not warn


def test_tcp_listening():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert app._tcp_listening("127.0.0.1", port)
    srv.close()
    assert not app._tcp_listening("127.0.0.1", port)
