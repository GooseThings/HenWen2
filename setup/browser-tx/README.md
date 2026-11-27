# Browser-transmitter spike (phase 1)

Proves the chosen architecture for browser-mic TX before any product code:
**browser WebRTC (JsSIP over WSS) → Asterisk PJSIP → `Rpt(<node>,P)`** —
phone-control mode, PTT = DTMF `*99`, unkey = `#`. Audio is G.711 µ-law
end-to-end (WebRTC-mandatory codec, app_rpt native — no transcoding, no
Opus module needed). No audio touches the HenWen/Flask process.

## HTTPS is required first

The TX button needs a secure browser context, which plain HTTP doesn't
provide off localhost. `setup-https.sh` provisions this via Apache + a
Let's Encrypt certificate for a public hostname pointed at this box — see
[Files](#files) below for its options (`--port`, `--dns-manual`).

If you don't have a public hostname or usable port forwarding at all
(CGNAT, a blocked ISP, a cloud box you don't want publicly exposed), that's
outside what these scripts automate — HenWen deliberately doesn't install
or manage a VPN/tunnel client on your box for you. Any third-party service
that can front HTTPS for a local port works instead (Tailscale Serve or
Funnel, Cloudflare Tunnel, a reverse proxy on another box you control,
etc.); set it up yourself, then either run `apply.sh` as usual or use the
Manager's TX Diagnostics page "Apply Browser TX Setup" button. Either way
it just needs a local Apache vhost fronting HenWen — `../apache-common.sh`
scans `/etc/apache2/sites-enabled/*.conf` live for whichever vhost(s)
actually proxy HenWen's own Flask port, patching every one it finds rather
than assuming a specific filename or that there's only ever one. A plain
`henwen.conf` (no cert of its own) is exactly what `setup-https.sh
--http-only` produces and what `scripts/install.sh` provisions by default on every
fresh install — but a hand-named vhost, or more than one at once (e.g. one
per hostname), is found the same way, so no manual renaming is ever
needed, even when the real TLS termination happens entirely on your
reverse proxy/tunnel, not on this box. Whatever you use
needs to proxy two things to this box: plain HTTP to Flask's port (default
5000), and a WebSocket-capable proxy for `/asterisk-ws` to the
`henwen-sip-ws` relay on `127.0.0.1:8087` (see `apply.sh`'s "Apache WSS
proxy" step for the exact line it inserts, if you're wiring that path up by
hand instead of via Apache).

**A reverse proxy only solves the signaling side.** Getting `wss://.../asterisk-ws`
reachable makes registration and dialing work, but the actual call audio
(WebRTC/SRTP) is separate, direct UDP — see "Network requirements" below.
A reverse proxy/tunnel that only carries HTTP(S)/WebSocket traffic
(Cloudflare Tunnel, Tailscale Serve/Funnel) does **not** carry this UDP
media, so `UDP 10000-10100` still needs to reach this box directly (a
router port forward, in the common case) regardless of how the signaling
path is fronted. `check-ports.sh`'s STUN/NAT probe is how to verify that
independently of everything else — see the table below.

## Files

- `setup-https.sh` — provisions Apache + a Let's Encrypt certificate for a
  public hostname you point at this box (`certbot --apache`).
  `sudo bash setup-https.sh [--port N] [--dns-manual] <hostname> <email>`.
  `--port N` serves HTTPS on a nonstandard port (useful when an ISP blocks
  forwarding ports below 1000 but allows a high port — the TX button
  adapts on its own, it just reads the port from the page's own URL).
  `--dns-manual` switches to a manual DNS-01 challenge for ISPs that block
  port 80 itself (Let's Encrypt's HTTP-01 validator always hits port 80
  externally, regardless of `--port`, so this is the only option in that
  case — trade-off is it doesn't auto-renew). Produces
  `/etc/apache2/sites-enabled/henwen-ssl.conf` by convention (`apply.sh`/
  `check-ports.sh` no longer require this specific name, but this script
  still always uses it) — even though certbot's own naming
  convention would otherwise call it `henwen-le-ssl.conf`.
  **Other apps on the box are preserved.** The vhost proxies `/` to HenWen,
  which would otherwise swallow every other URL on that hostname (issue
  #77: Allmon3 and AllScan started 404ing, while still working by raw IP
  since an IP doesn't match `ServerName`). So before claiming the hostname
  it scans the default `DocumentRoot` and emits `ProxyPass /<dir> !` for
  each directory already there, above the catch-all, and prints what it
  preserved. A directory whose name collides with one of HenWen's own URLs
  (`api`, `status`, `login`, `static`, …) can't be excluded without
  breaking HenWen, so those are reported as a warning instead and stay
  reachable by IP.
- `modules.snippet` — PJSIP/WebRTC module loads appended to `modules.conf`
  (ASL3 SIP-phone guide's set + websocket transport + SRTP)
- `pjsip.snippet` — WS transport + one `henwen-tx` endpoint (`webrtc=yes`,
  ulaw only, locked to a context that can dial exactly one extension)
- `extensions-custom.snippet` — that context, installed via the
  `#tryinclude "custom/extensions.conf"` hook ASL3 already ships
- `apply.sh` — backs everything up, applies the above, live-loads the
  modules (no Asterisk restart), installs and starts the `henwen-sip-ws`
  relay (below), adds the Apache `/asterisk-ws` WSS proxy pointing at it
  (configtest-gated), generates the SIP secret, prints test credentials.
  Derives the local node number from `rpt.conf` (same rule as `app.py`'s
  `get_node_numbers()`: first top-level `[NNNN]` stanza) and substitutes it
  into `pjsip.snippet`/`extensions-custom.snippet` — pass it explicitly as
  `sudo bash apply.sh <node>` to override. Discovers and patches *every*
  Apache vhost actually fronting HenWen via `../apache-common.sh` (shared
  with `setup/ws-audio/apply.sh`) — no hardcoded filename, no manual renaming,
  and no vhost left out if there's more than one. Also runnable from the
  Manager's TX Diagnostics page ("Apply
  Browser TX Setup" button, owner-only, via `POST /api/tx/apply`) instead
  of SSHing in, the same way `ws-audio`/`audiosocket-tap`'s own Apply
  buttons already work.
- `rollback.sh` — restores the backups (whichever vhost name `apply.sh`
  actually patched)
- `check-ports.sh` — verifies every network requirement (local listeners,
  WSS handshake, STUN/NAT probe from the RTP range) and reports PASS/FAIL
- `tx-test.html` — standalone debug page, deliberately NOT web-served
  (it was removed from `static/` once the kiosk integration landed); to
  use it, temporarily copy it into `static/` and remove it afterward. Once
  served through HenWen (and logged in as admin/superuser in that browser),
  it self-populates username/password/dial from `/api/tx/config` — no
  editing needed regardless of which node it's running on

## Network requirements (router port forwards)

The feature does not work without these — **be explicit about them at
install time**:

| Port | Proto | Direction | Purpose |
|------|-------|-----------|---------|
| 443 (or your `--port`) | TCP | inbound → this box | HTTPS kiosk + SIP-over-WSS signaling (Apache) |
| 80 | TCP | inbound → this box | Let's Encrypt HTTP-01 challenge — not needed with `--dns-manual` |
| 10000–10100 | UDP | inbound → this box | WebRTC RTP media (Asterisk; range set by apply.sh) |
| — | UDP | outbound | STUN to stun.l.google.com (both sides' ICE candidates) |

Asterisk's builtin HTTP server (8088) and the relay (8087) stay
loopback-only and must **not** be forwarded. LAN-only operators need none of the inbound forwards — ICE
host candidates connect directly. For that case, `sudo bash
setup/browser-tx/setup-https.sh --lan` provisions HTTPS with a self-signed
certificate covering this box's LAN IP(s) and hostname (no hostname, email
or Let's Encrypt; port 443, or 8443 if another vhost already owns 443),
then re-run `apply.sh` and `setup/ws-audio/apply.sh` to add their proxies to it.
Each device shows a certificate warning once; accept it and the page is a
secure context. `check-ports.sh` reads the recorded mode and skips the
STUN/forward checks.

Run `sudo bash setup/browser-tx/check-ports.sh` to verify all of the above from
the box itself (it also STUN-probes from inside the RTP range and reports
whether the NAT/forward preserves ports). Exposure profile of the RTP
forward: Asterisk binds ports only for active calls (kernel drops the
rest), active TX calls are DTLS-SRTP, and `strictrtp` (default on) locks
each session to its learned peer.

## Test procedure

0. If this box doesn't already have HTTPS: `sudo bash setup/browser-tx/setup-https.sh <hostname> <email>`
1. `sudo bash setup/browser-tx/apply.sh`
2. Open `https://<host>/static/tx-test.html`, paste the printed password.
3. Connect & Register → expect REGISTERED.
4. Call node → expect CALL CONFIRMED and node audio in the browser.
5. **Only with zero linked nodes:** hold PTT briefly and speak; verify the
   node keys (kiosk keyed indicator / `rpt show variables` RPT_RXKEYED).

## Rollback

`sudo bash setup/browser-tx/rollback.sh` — restores all configs; loaded modules
stay resident (harmless, unreferenced) until Asterisk next restarts.

## SIP WebSocket relay (`henwen-sip-ws`)

Apache proxies `/asterisk-ws` to `henwen/sip_ws_relay.py` on
`127.0.0.1:8087`, which relays to Asterisk on `127.0.0.1:8088`. app_rpt
forwards node-to-node text to every browser link, and Asterisk sends it as
a SIP MESSAGE in a WebSocket *text* frame. When that text isn't valid UTF-8
(the DVSwitch bridge's DMR metadata, for example), the browser is required
to close the connection, and every browser on the node dropped at once.
The relay replaces each invalid byte with `?`, one for one, so lengths are
unchanged; everything else passes through untouched.

- Its own systemd unit, so restarting HenWen doesn't drop calls. Restarting
  `henwen-sip-ws` does; the kiosk redials within seconds.
- `apply.sh` only points Apache at it once it is listening; if it can't
  start, Apache keeps proxying straight to Asterisk and the script warns.
- Re-running `apply.sh` (or **Apply Browser TX Setup**) moves an existing
  install onto the relay. `rollback.sh` removes it.
- Logs: `journalctl -u henwen-sip-ws`. A `replaced N invalid UTF-8 byte(s)`
  line (at most one a minute) means it just saved those connections.
- **Manager → TX Diagnostics** has a "SIP WebSocket relay" check.
