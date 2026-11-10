#!/usr/bin/env python3
"""Prove a SIP credential registers, with no browser and no pip packages.

Speaks just enough SIP-over-WebSocket (RFC 7118) to REGISTER against
Asterisk's builtin HTTP server, answer its digest challenge, and
un-REGISTER again. This is the same path the kiosk's JsSIP takes, minus
Apache's TLS termination: it connects straight to the loopback WebSocket
(`http show status` lists it as /ws), so it exercises the transport,
endpoint identification and auth -- the parts that decide whether a
per-user identity actually works -- and nothing else.

    sudo ./check-register.py --user henwen-u-n8gmz --auth-user N8GMZ --password '<secret>'
    sudo ./check-register.py --user henwen-tx --password "$(cat /etc/asterisk/henwen-tx.secret)"

Exit status 0 on a 200 OK to the authenticated REGISTER, 1 otherwise.
Stdlib only, on purpose: it has to run on a box with nothing but Python.
"""
import argparse
import base64
import hashlib
import os
import re
import socket
import sys
import time


def ws_connect(host, port, path, timeout):
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
           f"Sec-WebSocket-Protocol: sip\r\nSec-WebSocket-Version: 13\r\n\r\n")
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("connection closed during WebSocket handshake")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    if " 101 " not in head.split("\r\n", 1)[0]:
        raise RuntimeError("WebSocket upgrade refused: " + head.split("\r\n", 1)[0])
    if "sec-websocket-protocol: sip" not in head.lower():
        raise RuntimeError("server did not accept the 'sip' subprotocol")
    return s


def ws_send_text(s, text):
    payload = text.encode()
    mask = os.urandom(4)
    hdr = bytearray([0x81])
    n = len(payload)
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126); hdr += n.to_bytes(2, "big")
    else:
        hdr.append(0x80 | 127); hdr += n.to_bytes(8, "big")
    hdr += mask
    s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def ws_recv_text(s):
    def need(n):
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                raise RuntimeError("connection closed")
            b += c
        return b
    while True:
        b0, b1 = need(2)
        op = b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            n = int.from_bytes(need(2), "big")
        elif n == 127:
            n = int.from_bytes(need(8), "big")
        mask = need(4) if b1 & 0x80 else None
        data = need(n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if op == 0x8:
            raise RuntimeError("server closed the WebSocket")
        if op == 0x9:          # ping -> pong
            ws_send_text(s, "")  # (a text pong is tolerated by Asterisk; keeps this tiny)
            continue
        if op in (0x1, 0x2):
            return data.decode(errors="replace")


def md5(x):
    return hashlib.md5(x.encode()).hexdigest()


def digest_header(challenge, method, uri, user, password):
    params = dict(re.findall(r'(\w+)="?([^",]+)"?', challenge))
    realm, nonce = params.get("realm", "asterisk"), params["nonce"]
    qop = params.get("qop", "")
    cnonce, nc = os.urandom(8).hex(), "00000001"
    ha1, ha2 = md5(f"{user}:{realm}:{password}"), md5(f"{method}:{uri}")
    if "auth" in qop:
        resp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp = md5(f"{ha1}:{nonce}:{ha2}")
        extra = ""
    opaque = f', opaque="{params["opaque"]}"' if "opaque" in params else ""
    return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'response="{resp}", algorithm=MD5{opaque}{extra}')


def register(s, args, auth=None, expires=300):
    branch = "z9hG4bK" + os.urandom(6).hex()
    uri = f"sip:{args.domain}"
    lines = [
        f"REGISTER {uri} SIP/2.0",
        f"Via: SIP/2.0/WS {args.via_host};branch={branch}",
        "Max-Forwards: 70",
        f"To: <sip:{args.user}@{args.domain}>",
        f"From: <sip:{args.user}@{args.domain}>;tag={args.tag}",
        f"Call-ID: {args.call_id}",
        f"CSeq: {args.cseq} REGISTER",
        f"Contact: <sip:{args.user}@{args.via_host};transport=ws>",
        f"Expires: {expires}",
        "User-Agent: HenWen check-register",
        "Content-Length: 0",
    ]
    if auth:
        lines.insert(8, "Authorization: " + auth)
    args.cseq += 1
    ws_send_text(s, "\r\n".join(lines) + "\r\n\r\n")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        msg = ws_recv_text(s)
        first = msg.split("\r\n", 1)[0]
        if first.startswith("SIP/2.0 100"):
            continue
        if first.startswith("SIP/2.0"):
            return first, msg
    raise RuntimeError("no final response to REGISTER")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user", required=True, help="SIP user (From/To/Contact user; the endpoint name)")
    ap.add_argument("--auth-user", help="digest username (default: same as --user)")
    ap.add_argument("--password", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--path", default="/ws")
    ap.add_argument("--domain", help="SIP domain (default: --host)")
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()
    args.auth_user = args.auth_user or args.user
    args.domain = args.domain or args.host
    args.via_host = os.urandom(6).hex() + ".invalid"
    args.tag, args.call_id, args.cseq = os.urandom(5).hex(), os.urandom(8).hex(), 1

    try:
        s = ws_connect(args.host, args.port, args.path, args.timeout)
    except Exception as e:
        print(f"FAIL  WebSocket to ws://{args.host}:{args.port}{args.path}: {e}")
        return 1
    print(f"ok    WebSocket upgraded (subprotocol sip) at ws://{args.host}:{args.port}{args.path}")
    try:
        first, msg = register(s, args)
        print(f"info  REGISTER (no credentials) -> {first}")
        if first.startswith("SIP/2.0 401") or first.startswith("SIP/2.0 407"):
            m = re.search(r"^(?:WWW|Proxy)-Authenticate:\s*Digest\s+(.*)$", msg, re.M | re.I)
            if not m:
                print("FAIL  challenge without a Digest header"); return 1
            auth = digest_header(m.group(1), "REGISTER", f"sip:{args.domain}",
                                 args.auth_user, args.password)
            first, msg = register(s, args, auth=auth)
            print(f"info  REGISTER (digest as {args.auth_user!r}) -> {first}")
        if not first.startswith("SIP/2.0 200"):
            why = {"401": "wrong password or auth username", "403": "endpoint refused (identify/auth mismatch?)",
                   "404": "no endpoint named this (From user must be the endpoint name)",
                   "404 Not found": ""}.get(first.split(" ", 2)[1] if " " in first else "", "")
            print(f"FAIL  registration refused: {first}  {why}".rstrip())
            return 1
        print(f"PASS  {args.user!r} registered (auth as {args.auth_user!r})")
        # No un-REGISTER needed: Asterisk drops a WebSocket contact the
        # moment its transport closes (confirmed: `pjsip show contacts` is
        # empty right after this script exits).
        return 0
    except Exception as e:
        print(f"FAIL  {e}")
        return 1
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
