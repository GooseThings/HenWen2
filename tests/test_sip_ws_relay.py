"""Tests for henwen/sip_ws_relay.py: the UTF-8 cleaning, the downstream
frame filter, and one loopback run of the live relay against a fake
Asterisk."""
import socket
import struct
import threading

import pytest

import sip_ws_relay as r

# The body app_rpt actually sent from the DVSwitch bridge on 2026-11-26
# (captured with `pjsip set logger on`): DMR metadata, not text.
DMR_BODY = b'M 1999 0 \x08\x13.\x1e\xfa\x13\x1b\xfcq\x01~\xd3\x01\x01VE2HEW'


def sip_message(body):
    return (b'MESSAGE sip:abc@127.0.0.1:1;transport=ws;ob SIP/2.0\r\n'
            b'Content-Type: text/plain\r\n'
            b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)


def frame(opcode, payload, fin=True, rsv1=False, mask=None):
    b0 = (0x80 if fin else 0) | (0x40 if rsv1 else 0) | opcode
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n <= 125:
        head = bytes([b0, mbit | n])
    elif n <= 0xFFFF:
        head = bytes([b0, mbit | 126]) + struct.pack('>H', n)
    else:
        head = bytes([b0, mbit | 127]) + struct.pack('>Q', n)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        head += mask
    return head + payload


# ---- sanitize_utf8 ------------------------------------------------------

def test_valid_text_is_returned_unchanged():
    data = 'héllo ✓ 𝄞'.encode()
    clean, n = r.sanitize_utf8(data)
    assert clean is data and n == 0


def test_dmr_metadata_is_cleaned_without_changing_length():
    clean, n = r.sanitize_utf8(DMR_BODY)
    assert len(clean) == len(DMR_BODY)
    assert n == 3                       # \xfa, \xfc, \xd3 and nothing else
    clean.decode('utf-8')
    assert clean.endswith(b'VE2HEW') and clean.startswith(b'M 1999 0 ')


@pytest.mark.parametrize('bad', [
    b'\xc0\xaf',            # overlong '/'
    b'\xed\xa0\x80',        # UTF-16 surrogate
    b'\xf4\x90\x80\x80',    # past U+10FFFF
    b'abc\xe2\x82',         # truncated at the end
    b'\x80\x80',            # stray continuation bytes
])
def test_invalid_sequences_become_question_marks(bad):
    clean, n = r.sanitize_utf8(bad)
    assert len(clean) == len(bad) and n > 0
    clean.decode('utf-8')


# ---- DownstreamFilter ---------------------------------------------------

def test_valid_frame_passes_byte_identical():
    f = frame(r.OP_TEXT, sip_message(b'hello'))
    out, n = r.DownstreamFilter().feed(f)
    assert out == f and n == 0


def test_invalid_text_frame_is_rewritten_same_size():
    f = frame(r.OP_TEXT, sip_message(DMR_BODY))
    out, n = r.DownstreamFilter().feed(f)
    assert n == 3 and len(out) == len(f)
    hdr = r.parse_frame_header(out)
    payload = out[hdr[4]:]
    payload.decode('utf-8')
    assert b'Content-Length: 29' in payload


def test_byte_at_a_time_feed_matches_whole_feed():
    stream = (frame(r.OP_TEXT, sip_message(DMR_BODY)) + frame(0x9, b'ping')
              + frame(r.OP_TEXT, sip_message(b'ok' * 100)))
    whole, n_whole = r.DownstreamFilter().feed(stream)
    filt = r.DownstreamFilter()
    pieces, n_pieces = [], 0
    for i in range(len(stream)):
        out, n = filt.feed(stream[i:i + 1])
        pieces.append(out)
        n_pieces += n
    assert b''.join(pieces) == whole and n_pieces == n_whole == 3


def test_fragmented_text_is_checked_as_a_whole():
    # '✓' (e2 9c 93) split across two fragments is valid and must survive.
    a, b = 'tick ✓'.encode()[:6], 'tick ✓'.encode()[6:]
    stream = (frame(r.OP_TEXT, a, fin=False) + frame(0x9, b'p')
              + frame(r.OP_CONTINUATION, b, fin=True))
    out, n = r.DownstreamFilter().feed(stream)
    assert n == 0
    assert out == frame(0x9, b'p') + r.build_frame(r.OP_TEXT, 'tick ✓'.encode())


def test_binary_rsv1_and_masked_frames_pass_untouched():
    stream = (frame(r.OP_BINARY, b'\xff\xfe')
              + frame(r.OP_TEXT, b'\xff', rsv1=True)
              + frame(r.OP_TEXT, b'\xff', mask=b'\x01\x02\x03\x04'))
    out, n = r.DownstreamFilter().feed(stream)
    assert out == stream and n == 0


def test_large_frame_lengths_round_trip():
    body = b'x' * 70000 + b'\xff'
    f = frame(r.OP_TEXT, body)
    out, n = r.DownstreamFilter().feed(f)
    assert n == 1 and len(out) == len(f)


def test_oversized_message_is_refused():
    with pytest.raises(r.ProtocolError):
        r.DownstreamFilter(max_message=100).feed(frame(r.OP_TEXT, b'x' * 101))


def test_response_status_and_split_head():
    head, rest = r.split_head(b'HTTP/1.1 101 Switching Protocols\r\nA: b\r\n\r\nXYZ')
    assert rest == b'XYZ' and r.response_status(head) == 101
    assert r.split_head(b'HTTP/1.1 101') is None
    assert r.response_status(b'garbage') is None


# ---- loopback run of the live relay -------------------------------------

def _recv_until(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def test_live_relay_cleans_downstream_and_passes_upstream(monkeypatch):
    request = (b'GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n'
               b'Connection: Upgrade\r\nSec-WebSocket-Protocol: sip\r\n\r\n')
    response = b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Protocol: sip\r\n\r\n'
    bad = frame(r.OP_TEXT, sip_message(DMR_BODY))
    client_frame = frame(r.OP_TEXT, b'\xffraw from browser', mask=b'\x05\x06\x07\x08')
    seen = {}

    fake = socket.socket()
    fake.bind(('127.0.0.1', 0))
    fake.listen(1)

    def asterisk():
        conn, _ = fake.accept()
        seen['request'] = _recv_until(conn, len(request))
        conn.sendall(response + bad)
        seen['from_browser'] = _recv_until(conn, len(client_frame))
        conn.close()

    relay = socket.socket()
    relay.bind(('127.0.0.1', 0))
    relay.listen(1)
    monkeypatch.setattr(r, 'UPSTREAM_PORT', fake.getsockname()[1])

    def relay_once():
        conn, _ = relay.accept()
        r._handle(conn)

    threads = [threading.Thread(target=asterisk), threading.Thread(target=relay_once)]
    for t in threads:
        t.start()
    browser = socket.create_connection(relay.getsockname(), timeout=5)
    browser.sendall(request)
    got = _recv_until(browser, len(response) + len(bad))
    browser.sendall(client_frame)
    for t in threads:
        t.join(timeout=5)
    browser.close()
    fake.close()
    relay.close()

    assert seen['request'] == request
    assert seen['from_browser'] == client_frame          # upstream direction untouched
    assert got.startswith(response)
    out = got[len(response):]
    assert len(out) == len(bad) and out != bad
    hdr = r.parse_frame_header(out)
    out[hdr[4]:].decode('utf-8')


def test_port_probe_is_closed_quietly(capsys):
    # apply.sh checks the relay with a connect-and-close; that is not an error.
    a, b = socket.socketpair()
    a.close()
    r._handle(b)
    assert capsys.readouterr().out == ''


def test_truncated_request_is_logged(capsys):
    a, b = socket.socketpair()
    a.sendall(b'GET /ws HTTP/1.1\r\nHost: x\r\n')
    a.close()
    r._handle(b)
    assert 'closing connection' in capsys.readouterr().out
