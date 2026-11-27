#!/usr/bin/env python3
"""
HenWen SIP WebSocket relay: keeps browser SIP sockets alive by cleaning
text frames from Asterisk before they reach the browser.

Why it exists: app_rpt forwards node-to-node text (`M <src> <dest> <text>`)
to every link whose name does not start with `0` (app_rpt 3.10.8,
distribute_to_all_links()), and that includes each per-callsign browser
link. Asterisk turns each one into an in-dialog SIP MESSAGE and sends it
over the WebSocket as a *text* frame. The text is whatever the sender put
in it: the DVSwitch bridge, for one, sends binary DMR metadata. RFC 6455
says a text frame must be valid UTF-8 and that the receiver must fail the
connection when it isn't, and Chrome does exactly that. Every browser
linked to the node lost its SIP socket at the same instant, on every DMR
transmission (seen live 2026-11-26).

What it does: sits between Apache (`/asterisk-ws`) and Asterisk's
loopback HTTP server. The HTTP upgrade and everything the browser sends
pass through byte for byte. In the Asterisk -> browser direction it parses
frame boundaries and, in text messages only, replaces each byte that is
not part of valid UTF-8 with `?`. That keeps every length unchanged,
including the SIP Content-Length the bytes are counted in, so the message
still parses; the replaced bytes were never readable text anyway. Binary,
control and compressed (RSV1) frames pass through untouched.

Runs as its own systemd unit (`henwen-sip-ws`, installed by
setup/browser-tx/apply.sh), not as a HenWen child process, so restarting
HenWen does not drop anyone's call. Stdlib only, started with `python3 -I`:
it imports nothing from this repo. Cost is one thread per direction per
browser SIP connection, carrying signalling only (media is direct UDP);
idle connections block in recv().

The frame logic (`sanitize_utf8`, `parse_frame_header`, `build_frame`,
`DownstreamFilter`) is sans-I/O so tests/test_sip_ws_relay.py can cover it
without sockets.
"""
import os
import socket
import struct
import sys
import threading
import time

LISTEN_HOST   = os.environ.get('SIP_WS_RELAY_HOST', '127.0.0.1')
LISTEN_PORT   = int(os.environ.get('SIP_WS_RELAY_PORT', '8087'))
UPSTREAM_HOST = os.environ.get('SIP_WS_UPSTREAM_HOST', '127.0.0.1')
UPSTREAM_PORT = int(os.environ.get('SIP_WS_UPSTREAM_PORT', '8088'))
MAX_CONNECTIONS = int(os.environ.get('SIP_WS_MAX_CONNECTIONS', '256'))
REPORT_EVERY_SEC = 60

MAX_HEAD_BYTES = 16384      # an HTTP upgrade request/response head
MAX_MESSAGE_BYTES = 1 << 20  # one SIP message; real ones are a few KB
CONNECT_TIMEOUT_SEC = 5
RECV_BYTES = 65536

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2


class ProtocolError(Exception):
    """The peer sent something this relay refuses to forward."""


class ClosedBeforeRequest(Exception):
    """The peer connected and left without sending anything: a port check
    (setup/browser-tx/apply.sh, TX Diagnostics), not a broken client."""


def sanitize_utf8(data):
    """Return (clean_bytes, replaced): every byte of `data` that is not part
    of a valid UTF-8 sequence becomes b'?', one for one, so the length never
    changes. Valid input comes back as the same bytes object. Python's
    strict decoder rejects the same things a browser does (overlong forms,
    surrogates, code points past U+10FFFF, truncated sequences)."""
    try:
        data.decode('utf-8')
        return data, 0
    except UnicodeDecodeError:
        pass
    buf = bytearray(data)
    pos = 0
    replaced = 0
    while True:
        try:
            bytes(buf[pos:]).decode('utf-8')
            return bytes(buf), replaced
        except UnicodeDecodeError as e:
            start, end = pos + e.start, pos + e.end
            buf[start:end] = b'?' * (end - start)
            replaced += end - start
            pos = end


def parse_frame_header(buf):
    """Parse one frame header from the start of `buf`.

    Returns None until the whole header is present, else
    (fin, rsv1, opcode, masked, header_len, payload_len)."""
    if len(buf) < 2:
        return None
    b0, b1 = buf[0], buf[1]
    fin = bool(b0 & 0x80)
    rsv1 = bool(b0 & 0x40)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    hlen = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = struct.unpack('>H', bytes(buf[2:4]))[0]
        hlen = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = struct.unpack('>Q', bytes(buf[2:10]))[0]
        hlen = 10
    if masked:
        hlen += 4
    if len(buf) < hlen:
        return None
    return fin, rsv1, opcode, masked, hlen, length


def build_frame(opcode, payload):
    """One unmasked, final frame (server -> client frames are never masked)."""
    length = len(payload)
    b0 = 0x80 | (opcode & 0x0F)
    if length <= 125:
        head = bytes([b0, length])
    elif length <= 0xFFFF:
        head = bytes([b0, 126]) + struct.pack('>H', length)
    else:
        head = bytes([b0, 127]) + struct.pack('>Q', length)
    return head + payload


class DownstreamFilter:
    """Sans-I/O filter for the Asterisk -> browser byte stream (after the
    101 response). feed() takes whatever recv() returned and gives back the
    bytes to send on, plus how many invalid bytes it replaced.

    A text message split over several frames is held until its final frame
    so its UTF-8 is checked as a whole (a character may straddle frames),
    then sent on as one frame. Control frames may arrive in the middle of
    that and go straight through, as RFC 6455 allows. Unfragmented valid
    text frames go out byte-identical to how they came in."""

    def __init__(self, max_message=MAX_MESSAGE_BYTES):
        self._buf = bytearray()
        self._text = None        # bytearray while inside a fragmented text message
        self._max = max_message

    def feed(self, data):
        self._buf += data
        out = bytearray()
        replaced = 0
        while True:
            hdr = parse_frame_header(self._buf)
            if hdr is None:
                break
            fin, rsv1, opcode, masked, hlen, plen = hdr
            if plen > self._max:
                raise ProtocolError(f'frame of {plen} bytes exceeds {self._max}')
            total = hlen + plen
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            del self._buf[:total]

            if opcode >= 0x8 or masked or rsv1:
                # Control frames, a (non-compliant) masked server frame, or
                # a compressed one: nothing here can safely rewrite those.
                out += frame
                continue
            payload = frame[hlen:]
            if opcode == OP_TEXT or (opcode == OP_CONTINUATION and self._text is not None):
                if opcode == OP_TEXT:
                    self._text = bytearray()
                self._text += payload
                if len(self._text) > self._max:
                    raise ProtocolError(f'text message exceeds {self._max} bytes')
                if fin:
                    message = bytes(self._text)
                    self._text = None
                    clean, n = sanitize_utf8(message)
                    if n == 0 and opcode == OP_TEXT:
                        out += frame          # unfragmented and valid: untouched
                    else:
                        out += build_frame(OP_TEXT, clean)
                    replaced += n
                continue
            out += frame                      # binary frames and their continuations
        return bytes(out), replaced


def split_head(buf):
    """(head_including_blank_line, rest) once b'\\r\\n\\r\\n' is in buf, else None."""
    i = buf.find(b'\r\n\r\n')
    if i < 0:
        return None
    return bytes(buf[:i + 4]), bytes(buf[i + 4:])


def response_status(head):
    """Status code from an HTTP response head, or None."""
    try:
        return int(head.split(b'\r\n', 1)[0].split()[1])
    except (IndexError, ValueError):
        return None


# ---- live relay ---------------------------------------------------------

_stats_lock = threading.Lock()
_stats = {'messages': 0, 'bytes': 0, 'last_report': 0.0}


def _log(msg):
    print(f'[SIP-WS] {msg}', flush=True)


def _note_replaced(n):
    """Count replacements; log a summary at most once a minute so a busy
    DMR talkgroup can't flood the journal."""
    now = time.monotonic()
    with _stats_lock:
        _stats['messages'] += 1
        _stats['bytes'] += n
        if now - _stats['last_report'] < REPORT_EVERY_SEC:
            return
        msgs, nbytes = _stats['messages'], _stats['bytes']
        _stats['messages'] = _stats['bytes'] = 0
        _stats['last_report'] = now
    _log(f'replaced {nbytes} invalid UTF-8 byte(s) in {msgs} SIP message(s) from Asterisk '
         '(app_rpt text from a linked node; browsers would have dropped the connection)')


def _read_head(sock):
    buf = bytearray()
    while True:
        parts = split_head(buf)
        if parts is not None:
            return parts
        if len(buf) > MAX_HEAD_BYTES:
            raise ProtocolError('HTTP head too large')
        chunk = sock.recv(RECV_BYTES)
        if not chunk:
            if not buf:
                raise ClosedBeforeRequest()
            raise ProtocolError('connection closed during HTTP head')
        buf += chunk


def _close(*socks):
    for s in socks:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass


def _pipe_raw(src, dst):
    try:
        while True:
            chunk = src.recv(RECV_BYTES)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        _close(src, dst)


def _handle(client):
    upstream = None
    try:
        head, rest = _read_head(client)
        upstream = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=CONNECT_TIMEOUT_SEC)
        upstream.settimeout(None)
        upstream.sendall(head + rest)
        threading.Thread(target=_pipe_raw, args=(client, upstream), daemon=True).start()

        resp_head, resp_rest = _read_head(upstream)
        client.sendall(resp_head)
        if response_status(resp_head) != 101:
            if resp_rest:
                client.sendall(resp_rest)
            _pipe_raw(upstream, client)
            return
        filt = DownstreamFilter()
        data = resp_rest
        while True:
            if data:
                out, n = filt.feed(data)
                if out:
                    client.sendall(out)
                if n:
                    _note_replaced(n)
            data = upstream.recv(RECV_BYTES)
            if not data:
                break
    except ProtocolError as e:
        _log(f'closing connection: {e}')
    except (ClosedBeforeRequest, OSError):
        pass
    finally:
        _close(client)
        if upstream is not None:
            _close(upstream)


def serve():
    slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(64)
    _log(f'listening on {LISTEN_HOST}:{LISTEN_PORT}, relaying to Asterisk at '
         f'{UPSTREAM_HOST}:{UPSTREAM_PORT}')

    def run(conn):
        try:
            _handle(conn)
        finally:
            slots.release()

    while True:
        conn, _addr = srv.accept()
        if not slots.acquire(blocking=False):
            _log(f'connection limit ({MAX_CONNECTIONS}) reached, refusing one')
            _close(conn)
            continue
        threading.Thread(target=run, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    try:
        serve()
    except KeyboardInterrupt:
        sys.exit(0)
