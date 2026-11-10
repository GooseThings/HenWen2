# HenWen 2.0 — audio as native Asterisk VoIP

**Status:** Phase 0/1 in progress. Approved 2026-11-10.
**Repo:** `GooseThings/HenWen2`, forked from `GooseThings/HenWen` at `c875598`
(full history preserved; `henwen1` remote is fetch-only).

## Why this fork exists

HenWen 1 monitors a node by **tapping** it: ChanSpy or MixMonitor on the
node's own radio channel, raw PCM into `audio_relay.py`, paced into 20ms
frames, encoded by ffmpeg, delivered as WebM/MSE or raw Opus over a
WebSocket. Everything downstream of that choice — the live-edge controller,
the jitter buffer, the declick ramps, the AudioSocket tap and its circuit
breaker, the `TAP_CHANNEL_TECHS` allowlist, the self-echo mute hold — exists
to compensate for it.

The tap captures **both directions of the radio channel**, so it necessarily
carries the transmit mix. A browser-TX operator's own voice is part of that
mix by design, which is why they hear themselves after unkeying (HenWen 1
issue; mitigated there by holding the RX mute past the unkey, which hides the
echo rather than removing it).

Asterisk already offers the right interfaces, and HenWen 1 uses one of them
for TX only:

- **A call is a link.** `CONF`/`TXCONF` are Asterisk softmix bridges. Softmix
  subtracts each member's own audio from its own mix, so **every link gets a
  clean mix-minus-self duplex feed**. No echo, no server-side pipeline, no
  latency estimation.
- **`outstreamcmd`** hands a configured process the node's transmit-side audio
  (monchannel, a TXCONF member) on stdin as raw 8kHz slin — exactly what
  `audio_relay.py` already consumes.

See `docs/app-rpt-audio-notes.md` for the source-level verification behind
both claims.

**HenWen 2.0's rule: every audio stream is a native Asterisk VoIP call.**
Links for people, `outstreamcmd` for machines.

## Decisions

| ID | Decision | Status |
|----|----------|--------|
| D1 | How TX is restricted | **Software-only** (owner's call). Per-user credentials make abuse attributable and revocable; every `*99` is logged server-side. D1-c spiked in Phase 1; D1-b held in reserve. |
| D2 | Does an explicit "arm" step stay before keying RF? | **Answered: no.** Connecting is enough. Three controls: **PTT** (hold to talk), **MUTE** (silences received audio), **RECORD** (unchanged). See "D2 as decided" below. |
| D3 | HTTPS mandatory (no choice — WebRTC); do we ship coturn? | Open — see "D3: how to think about TURN" below |
| D4 | Repo visibility | Defaulted to **private** at creation; unanswered. `gh repo edit GooseThings/HenWen2 --visibility public` flips it. Note `update.sh`'s self-update fetches over plain HTTPS and needs public (Phase 6 concern only). |

### D1 — the security consequence, stated plainly

With everyone on `Rpt(<node>,P)`, **the SIP credential itself is
transmit-capable**: `*99` works from any SIP client, so `users.listen_only`
is advisory, not enforced. `phone_functions` is node-wide
(`myrpt->p.phone_functions`), so a restricted function stanza can't be
selected per call.

- **D1-a (chosen)** — software-only. Per-user credentials, instant revocation,
  server-side logging of key events. Residual risk accepted.
- **D1-b (reserve)** — a second extension on a node whose `functions` stanza
  omits `99 = cop,6`. Still a full native duplex call — PTT simply isn't
  wired. Enforced server-side. Not the same as `Rpt(...,Pm)`, which was
  rejected.
- **D1-c (spike)** — whether `rpt_auth.c` offers per-link command
  authorization.

### D2 as decided — no arm step

The kiosk gets three controls and no arming:

- **PTT** — hold to transmit. This is the only thing that keys the node.
- **MUTE** — silences incoming audio. Local to the browser; it does not
  change the call or the node.
- **RECORD** — unchanged from 1.x.

Connecting establishes the call, and the call is keyable from that moment,
which is consistent with D1-a: the credential is keyable anyway, so an arm
step was never a real gate — it was a UI speed bump.

**One consequence to design around: when does the browser ask for the
microphone?** A WebRTC call that can transmit needs an audio track, and
getting one triggers the permission prompt. With no arm step there are two
shapes, and they are not equivalent:

1. **Ask at connect.** Simple, and the outbound RTP it produces is what keeps
   `rtp_timeout=60` satisfied (see spike 3). Cost: someone who only wants to
   listen still gets a microphone prompt, which on a public-ish kiosk reads
   as alarming.
2. **Start receive-only, renegotiate on the first PTT press.** No prompt
   until someone actually wants to talk. Cost: a receive-only call sends no
   RTP, which is exactly what `rtp_timeout=60` hangs up — so this needs the
   timeout handled deliberately rather than inherited.

Phase 3 has to pick one. Option 1 is the safe default; option 2 is the nicer
behavior and needs the `rtp_timeout` question answered first.

### D3: how to think about TURN

**The problem is not HTTPS.** The web page rides your HTTPS connection; the
*audio* does not. WebRTC media is a separate UDP stream from the listener's
browser straight to this box (ports 10000-10100). So a listener can load the
board perfectly and still get no audio, because the two travel different
paths.

That is a genuine regression against 1.x, whose audio rode the same HTTPS
connection as the page and therefore worked through anything that passed web
traffic — including HTTP-only tunnels like Cloudflare Tunnel and Tailscale
Serve, which carry no UDP at all.

Who it breaks, roughly in order of likelihood: a listener on a corporate or
school network that only allows TCP 443; hotel and some public wifi; a few
mobile carriers; and both ends behind symmetric NAT. Ordinary home broadband
with STUN — which browser TX already uses — is fine, and is probably most of
this node's listeners.

The three ways to answer it:

1. **coturn on this box.** Free, no third party. But it needs its own public
   ports (3478, and realistically 5349/TLS on **443** to rescue the hard
   cases — which collides with Apache already holding 443 here), and relayed
   audio comes *in and goes back out* over the same residential uplink,
   doubling bandwidth for every relayed listener.
2. **A hosted TURN service** (Cloudflare, Twilio, Xirsys, metered.ca). No
   ports to forward, no bandwidth on your uplink, cheap or free at this
   scale. Costs: an external dependency with credentials to manage, and a
   third party handling the (encrypted) media packets.
3. **Keep the legacy HTTP audio path as the fallback.** The listener whose
   WebRTC cannot connect gets 1.x's streaming path instead. This cuts
   against Phase 5's "delete the old pipeline" and is the honest trade for
   it: the old path is the only thing that works where UDP does not.

**Recommended way to look at it: measure before buying.** Do not decide this
from first principles — instrument it. Browser TX already has the telemetry
shape for it (`pktsSent=0` means ICE never completed). Phase 3 should log,
per listener, whether media ever flowed. Then the question stops being "might
someone fail?" and becomes "how many actually did, and from where". If the
answer is nobody, STUN alone is the right answer and TURN is unbought
complexity.

## Architecture

### Per-user SIP identity

Each HenWen account becomes a real Asterisk endpoint:

- `users.callsign` (new column, validated, unique) plus a per-user SIP secret.
- Its own PJSIP endpoint/auth/aor, in a context exposing **only** its own
  node's extension — keeping HenWen 1's "a leaked credential can't dial
  anywhere else" property.
- app_rpt link name `0<callsign>`: `0`-prefixed so app_rpt doesn't announce
  connects (HenWen 1 v2026.10.28.2 fixed exactly this), unique per user so
  links stay unambiguous in app_rpt's container. app_rpt's duplicate-name
  kill only applies to all-digit names, so same-named sessions would pile up
  ambiguously.
- Secret rotates on password change (reuse the `password_epoch` pattern) and
  on revoke/delete.

Side benefit: `rpt lstats` and the Connected Nodes list start showing the
real callsigns of who is listening.

Provisioning: spike PJSIP realtime config (sorcery + `res_config_sqlite3`) so
a user row *is* a SIP account with no reload. Fall back to a generated
include file plus `pjsip reload`.

### One call is the whole audio path

Connect places the call; RX mute is page-side; TX is `*99`/`#`.

**Mute must never be `track.enabled = false`.** `tx-spike/pjsip.snippet` sets
`rtp_timeout=60` because a browser that vanishes otherwise leaves
`Rpt(<node>,P)` up forever (confirmed in HenWen 1 on a 27-hour-old phone-mode
channel). A muted mic therefore has to keep sending RTP: mute is
gain-to-zero in the existing WebAudio chain, and RX mute is playback-side
only.

### Retired with this architecture

`audio_ws_relay.py`, `ws-audio/`, `audiosocket-tap/`, `_start_broadcast()`,
`_start_capture_only()`, `_tap_breaker`, `TAP_CHANNEL_TECHS`, the MixMonitor
path, `/api/audio/stream/*`, the MSE live-edge controller, the WebCodecs/
AudioWorklet path, and the post-unkey self-echo hold. A large net deletion.

### Server-side consumers stay server-side

Recording and the Broadcastify/YouTube relay move to `outstreamcmd`, not to a
link, because **TXCONF carries the real on-air mix including telemetry**.
Telemetry joins `CONF` only for `ID1`, `PLAYBACK`, `TEST_TONE` and
`STATS_GPS_LEGACY`; courtesy tones and `LOCALPLAY` (which is how HenWen's
announcer, Node ID and NWS alerts reach the air) join **TXCONF only** and
reach links solely via the idle-gated altaudio whisper. A link-based
recording would silently miss a mid-QSO forced ID — unacceptable where "what
went out on air" is the product.

`audio_relay.py` survives in reduced form; ffmpeg survives only for
recording/relay encode.

## Hardware floor

**The Pi Zero 2 W floor is removed.** New floor: Raspberry Pi 4 (4GB) or any
2+ core x86-64 — which is already what HenWen 1's YouTube carve-out
recommended, so that exception collapses into the floor and disappears.

New scaling axis, to be measured rather than assumed: **concurrent WebRTC
sessions**. Cost moves from per-node (one ffmpeg serving everyone) to
per-listener (bridge member + link thread + DTLS-SRTP).

## Phases

| Phase | Work | Exit criteria |
|---|---|---|
| **0** | Repo, history, remotes; `/opt/HenWen2` + unit + DB + port; docs; floor rewrite | HenWen2 runs alongside HenWen 1, 1.x untouched |
| **1** | Spikes on the throwaway lab Asterisk (own dirs, AMI 15038 — **never** 643930) | Every question answered in writing before 2.0 audio code |
| **2** | `users.callsign`, per-user SIP provisioning, per-user dialplan/context, revocation | A second account registers and hears node audio on its own credential |
| **3** | Frontend: one call = audio; connect / RX mute / PTT; gain-to-zero mute | Two accounts connected at once, neither hears itself, PTT keys |
| **4** | Recording + stream relay onto `outstreamcmd` | A mid-QSO forced ID provably appears in a recording |
| **5** | Delete the old pipeline and its tests; wire `listen_only`; exclude session links from Smart Connector `ilink 11`, idle detection, link counts, board display | Full suite green on a smaller codebase |
| **6** | Scaling measurement, concurrency cap, TURN, 1.x→2.0 migration | Documented concurrent-session ceiling |

### Phase 1 spike list

1. Is the call downlink self-free **live**? (The source says yes; nothing has
   been heard yet. This gates the whole direction.)
2. D1-c: does `rpt_auth.c` offer per-link command authorization?
3. Does a muted mic keep sending RTP, or does `rtp_timeout=60` kill the call?
4. Is PJSIP realtime config (`res_config_sqlite3`) available in the ASL3 build?
5. `outstreamcmd`: actual content and actual cost.

## Risks

1. **Reach regression (highest).** WebRTC needs UDP media; HenWen 1's MSE path
   reached listeners through any HTTP proxy or tunnel. Without TURN, some
   remote listeners lose audio entirely. D3.
2. **Per-listener cost is unmeasured.** No claim that this is cheaper until
   Phase 6 measures it.
3. **Listeners are real links.** Smart Connector's per-peer `ilink 11` would
   disconnect them if not excluded (HenWen 1 issue #219 was this class of
   trap). Phase 5.
4. **Secret storage.** Per-user SIP secrets must be retrievable server-side to
   hand to a session, so plaintext at rest (precedent: Discord webhook,
   NickServ password) unless an encryption layer is added.
5. **Live node safety.** No dialplan or `rpt.conf` edits on 643930 before
   Phase 6. `rpt.conf` changes need `module reload app_rpt.so`, never
   `rpt restart`.

## Testing

The throwaway lab Asterisk becomes a first-class fixture: a lab node with a
continuously-emitting dummy rxchannel, driven by `sipp`/`pjsua` as a headless
SIP client. That makes "is the operator's own audio absent from their own
downlink?" an automated assertion — tone in, assert absent on that leg,
assert present on a second leg — **with no RF keyed**. Playwright for the
WebRTC path later. HenWen 1's pure-logic and route-smoke tests carry over
mostly unchanged; the audio-pipeline tests retire with the pipeline.

---

## Phase 0 — as built (2026-11-10)

**Repo.** `GooseThings/HenWen2`, created **private** (D4 was unanswered; this
is the reversible direction — `gh repo edit GooseThings/HenWen2 --visibility
public` flips it, and nothing is exposed by doing so, since the forked history
is already public via HenWen 1). Deliberately **not** a GitHub fork: a fork's
pull requests default to targeting upstream, which is exactly how 2.0 work
would accidentally land in 1.x. Full history and tags were pushed to a
standalone repo instead; the `henwen1` remote is fetch-only (its push URL is
set to `DISABLED`).

**Parallel install.** `/opt/HenWen2` is both the dev checkout and the install,
mirroring how `/opt/HenWen` works in 1.x. Unit `HenWen2.service`, user
`asterisk`, gunicorn on **127.0.0.1:5001** — loopback only, so Apache is the
only way in. Everything that could collide with the live 1.x install is
separate:

| | 1.x | 2.0 |
|---|---|---|
| Unit / path | `HenWen` / `/opt/HenWen` | `HenWen2` / `/opt/HenWen2` |
| HTTP | `0.0.0.0:5000` | `127.0.0.1:5001` |
| Database | `/etc/asterisk/henwen.db` | `/etc/asterisk/henwen2.db` (**empty**) |
| Sounds | `.../sounds/henwen` | `.../sounds/henwen2` |
| TTS voices | `henwen_tts_voices` | `henwen2_tts_voices` |
| Audio relay ports | 8099 / 8098 | 8199 / 8198 |

The empty database is a safety property, not an oversight: with no
announcements, connectors, Node ID monitors or stream-relay targets, 2.0's
background threads no-op, so the two instances cannot both fire scheduled
playback or connector actions at the same node. 2.0 starts at its own
first-run Create Account screen.

`/opt/HenWen2` is chowned to `asterisk`, so git there needs
`git config --global --add safe.directory /opt/HenWen2`.

**Reachable at `https://henwen.ironicsandwich.com/2/index.html`.** The
existing vhost got a `ProxyPass /2` above its `ProxyPass "/"` — order matters,
`/` would otherwise swallow `/2/` first. The vhost was backed up with a path
manifest to `/root/henwen2-apache-backup-<ts>/` and `apache2ctl configtest`
gated the reload. 1.x keeps the vhost root.

`/index.html` is an alias on the existing `/` view rather than a new route, so
it keeps the endpoint name `status_board` and every `check_auth()`
endpoint-name check (including the first-run `_SETUP_PAGES` bounce) applies to
it unchanged. **Decorator order is load-bearing:** decorators apply bottom-up,
so the lower `@app.route` registers first and `url_for('status_board')` builds
from that one. With the alias registered first, every redirect to the board
came out as `/index.html` — caught by `tests/test_routes_smoke.py`. Keep `"/"`
below the alias.

**Path-prefix support is a real feature, not scaffolding.** Phase 0 first
shipped a blunt version (a WSGI prefix strip plus a regex pass over rendered
HTML). It was replaced the same day by the proper fix, written against 1.x and
cherry-picked here — **HenWen 1 PR #222**:

- `_ScriptNamePrefixFix` sets `SCRIPT_NAME` from `URL_PREFIX` or the
  `X-Forwarded-Prefix` header (the latter trusted only from the local proxy,
  like `_LocalProxyFix`'s own headers), so Flask's routing, `url_for()`,
  redirects and the session cookie path are correct — with either proxy shape,
  prefix passed through or already stripped.
- The 27 `href`/`src`/`action` attributes are prefixed from
  `request.script_root`, and `templates/_basepath.html` carries a base-path
  adapter for the ~274 hardcoded URLs in inline JS: it patches `fetch`,
  `XMLHttpRequest` and `EventSource`, and exposes `henwenUrl()` for the five
  sites that bypass all three.
- `/asterisk-ws` and `/ws-audio` are deliberately **not** prefixed — they are
  Apache-level proxies installed at the vhost root by their own `apply.sh`,
  and they don't move with the Flask mount.

Doing it in 1.x means the fix lands in the version people actually run, and
Phase 3's frontend rewrite inherits a mount-anywhere app instead of a deletion
chore.

One finding worth keeping: prefixing `href`/`src`/`action` is **load-bearing
for correctness, not cosmetics.** `login.html`'s form is `action="/login"`, so
before it was prefixed, submitting the 2.0 Create Account form POSTed to the
**1.x** app at the vhost root — this instance's credentials to the other
instance. Found by diffing the rendered page's root-absolute references, now
covered by `tests/test_url_prefix.py`. Any new page still deserves the same
check:

```bash
curl -s https://henwen.ironicsandwich.com/2/<page> \
  | grep -oE '(src|href|action)="/[^"]*"' | sort -u   # every hit must start /2/
```
