# HenWen 2.0 — audio as native Asterisk VoIP

**Status (2026-11-11):** Phases 0-3 **done**. PRs #1, #2 and #3 are merged
to `main`, and `/opt/HenWen2` runs `main`. Phase 3's exit criterion was met
live on 2026-11-10 (three accounts at once, details below). Next is issue #5
(see "Next action"), then Phase 4.
**Repo:** `GooseThings/HenWen2`, forked from `GooseThings/HenWen` at `c875598`
(full history preserved; `henwen1` remote is fetch-only).

## Where things stand, and the next action

Written for whoever picks this up next — a different session, or a different
model. Read this section, then `docs/phase1-spikes.md` for what was measured
and `docs/app-rpt-audio-notes.md` for how app_rpt actually moves audio.

**HenWen2 is production.** It serves `https://henwen.ironicsandwich.com/` on
`0.0.0.0:5000` as unit `HenWen2` from `/opt/HenWen2`; `HenWen` (1.x) is
stopped and `disable`d. The 2.0 database is a copy of 1.x's, so
`SOUNDS_DIR`/`TTS_VOICES_DIR` point at the **1.x** directories on purpose.
Rollback is `systemctl stop HenWen2 && systemctl enable --now HenWen`; 1.x's
own database is untouched. Backups: `/root/henwen-cutover-20261110-103223/`.

**What is deployed:** `main` (merge of PR #3). The include wiring
(`sip-users/apply.sh`) and the sudoers line have been run by the owner.
Accounts with a callsign (N8GMZ, K8DIY, WO8E so far) get the one-call path;
every other account still gets the 1.x Listen and Arm TX unchanged.

**Phase 2 exit (2026-11-10 17:13):** `check-register.py` got 200 OK as
`henwen-u-n8gmz` / auth `N8GMZ` — the endpoint-name-vs-auth-user split is
confirmed. Listen from a remote browser: `sip-connected`, `sip-media
ice=connected rx=290 tx=292 jitter=2ms`, PTT as `*99`/`#` DTMF, steady
`sip-stats` (~50 pkt/s each way, 0.1% loss).

**Phase 3 exit (2026-11-10, ~18:20-19:55):** three accounts (N8GMZ, K8DIY,
WO8E) on three different home networks, on the node at the same time;
owner reports no issues — nobody heard themselves, PTT keyed from each.
Journal: 12 `sip-connected`, every one followed by `sip-media
ice=connected` (jitter 2-12ms), **zero `sip-fallback`/`sip-nomedia`** — all
on STUN alone. One real mid-call drop (K8DIY, 18:47:51, JsSIP `sip-ended:
Connection Error`, a phone client, right after a PTT press) recovered by the
automatic retry within 3s; it was not caused by a provisioning reload (the
last one was 18:44:45). This is a small sample for D3: it says STUN reaches
ordinary home networks, not that TURN is never needed.

**Resolved: session links are invisible to app_rpt's reporting — by our own
choice of name.** app_rpt 3.10.8 skips every link whose name starts with
`0` in `rpt lstats` (`rpt_cli.c:396`), `rpt stats`/`rpt nodes`
(`rpt_cli.c:287/596`), the AMI `RPT_ALINKS`/`RPT_LINKS` export
(`rpt_manager.c:111/244/578`), the link list sent to peers and to
stats.allstarlink.org (`rpt_link.c:344/391/507`), and connect telemetry
(`rpt_telemetry.c`). The `0` prefix was chosen to suppress the connect
announcement, and it suppresses all of the above with it. Consequences:

- The old "side benefit" (Connected Nodes shows who is listening) does not
  happen on its own. It has to be built from HenWen's own data — issue #5.
- Issue #4 (show SIP users on stats.allstarlink.org) is not reachable while
  the prefix stays. Dropping it would bring back the connect announcement
  and put every listener into the link list propagated to peer nodes across
  the network. Parked as the owner's decision; not recommended now.
- `_sip_session_link()`'s exclusions (connector `ilink 11`, connect gate,
  alerts) are guarding against links app_rpt never reports through those
  paths. Harmless; review them in Phase 5 rather than delete now.

**Next action, in order:**

1. **Issue #6** — Mute button height. Small CSS fix.
2. **Issue #5, built server-side.** List connected session users from the
   live `PJSIP/henwen-u-<callsign>-*` channels (callsign from the endpoint
   name), not from app_rpt. Keyed status from the AMI `DTMFEnd` events on
   those channels (`*99` keys, `#` unkeys), **not** from the browser's
   `tx-dtmf` client-log lines. That second part also closes a real D1-a gap:
   today the attributable "who keyed" record is reported by the browser, and
   a non-HenWen SIP client holding a valid credential would simply not send
   it. Asterisk sees the digits regardless (they are in `messages.log`), so
   an AMI-sourced, server-side key log per callsign is what D1-a actually
   promised. Show them as their own kind of row (callsign, keyed state), not
   disguised as nodes.
3. **Phase 4** — recording and stream relay onto `outstreamcmd`.

**Do not** press "Launch Updater": `update.sh`'s `REPO_URL` still points at
the 1.x repo, so it would reset this checkout onto 1.x. Held for Phase 6 by
the owner's decision.

**Testing reality:** `./venv/bin/pytest` from `/opt/HenWen2`, 1114 passing.
Node **643930 is radioless** (UCI-120, hand mic, no transceiver), so keying
it is not RF — that is what made both Phase 1 spikes possible without a
separate lab Asterisk. The spike harness is still installed; see
`henwen-spike-selfecho` in `/etc/asterisk/custom/extensions.conf` and the
threadless `[1998]` stanza in `rpt.conf`.

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
| D3 | HTTPS mandatory (no choice — WebRTC); do we ship coturn? | **Answered: start with STUN + the legacy fallback**, instrumented. Owner's preference was coturn + TLS-ALPN demux and they accepted starting simpler. See "D3 as decided" below. |
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

**Microphone permission is requested up front, at connect.** Decided by the
owner: RX and TX are what HenWen *is*, so microphone access is a core
prerequisite rather than a per-feature escalation, and an account only exists
because an admin created it.

That also settles spike 3's loose end in the simplest direction: a call with a
live audio track sends RTP continuously, which is exactly what
`rtp_timeout=60` needs to see, so a listening-only session stays up with
nothing extra. The rejected alternative — start receive-only and renegotiate
on the first PTT press — would have avoided the prompt for pure listeners but
sends no RTP, which is precisely what that timeout hangs up.

Reminder for Phase 3: mute is **gain-to-zero**, never
`track.enabled = false`, for the same reason.

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

1. **coturn on this box.** Free, no third party, and — correcting an earlier
   claim in this document — it costs **no extra bandwidth**. The doubling
   worry applies to relaying between two *external* peers; here one peer is
   this box, so a relayed listener's media arrives on the uplink once and
   leaves once, exactly as a direct connection would. The hop to Asterisk is
   loopback.

   The real cost is ports. On 3478/5349 it is easy to run and fixes symmetric
   NAT and some UDP-blocked cases. It does **not** fix the case that actually
   motivates TURN — a network permitting only TCP 443 — unless coturn listens
   on 443 itself, which Apache already holds here. Sharing 443 needs a
   TLS-ALPN demultiplexer in front (sslh, HAProxy) or a second IP. That
   demux, not the relay, is where the complexity lives.
2. **A hosted TURN service** (Cloudflare, Twilio, Xirsys, metered.ca). No
   ports to forward, no bandwidth on your uplink, cheap or free at this
   scale. Costs: an external dependency with credentials to manage, and a
   third party handling the (encrypted) media packets.
3. **Keep the legacy HTTP audio path as the fallback.** The listener whose
   WebRTC cannot connect gets 1.x's streaming path instead. This cuts
   against Phase 5's "delete the old pipeline" and is the honest trade for
   it: the old path is the only thing that works where UDP does not.

**Is WebRTC-plus-TURN the right approach, or guesswork?** It is the standard
one, and that is worth stating plainly, because "we might need a relay
server" can read like drift. A browser cannot open a raw socket, so WebRTC is
the *only* way a browser can be a native SIP endpoint — which is the entire
property 2.0 is built on, and the thing spike 1 measured. WebRTC in turn
mandates ICE, and ICE's defined answer to "no direct path exists" is a TURN
relay. Every serious browser-audio product ships one. TURN is not a patch
over a bad choice; it is the designed completion of the chosen one.

The more reassuring datapoint is local: **WebRTC already works on this node.**
1.x's browser TX has run over it for months for remote operators, on STUN
alone — the ICE problems it hit were diagnosed and fixed (STUN on both sides
plus the UDP 10000-10100 forward). So the open question is not "will this
transport work here", it is "does the listener population reach networks the
TX operators never did".

That population is narrower than it first looks: 2.0 audio requires a login
and accounts are admin-created, so listeners are the same vetted handful as
the operators, not the public.

### D3 as decided — STUN plus the legacy fallback, instrumented

The owner's instinct was coturn here with a TLS-ALPN demux plus the legacy
fallback, "if the complexity can be overcome", and they accepted starting
with STUN plus the fallback. Starting simpler, for a reason worth writing
down:

**With the legacy path kept as a fallback, TURN stops being a robustness
feature.** The legacy path rides the same HTTPS connection as the page, so it
already covers every case TURN would cover — TCP-443-only networks,
HTTP-only tunnels, blocked UDP, symmetric NAT. What TURN adds on top is
*low-latency audio instead of the ~1-3s legacy stream* for listeners on
hostile networks. That is a quality upgrade for an edge population, not a
rescue.

**And the "robust" option is the more fragile one.** Putting sslh or HAProxy
in front of 443 means every HTTPS request to this site — the kiosk included —
traverses another daemon. If it is misconfigured or dies, the whole web UI
goes down. That is a large blast radius to accept for an edge-case latency
improvement. A hosted TURN service solves the same TCP-443 case with **no
demux and no new daemon in the request path**, which makes it strictly safer
than self-hosting here, the opposite of the usual intuition.

So the order is:

1. **Now:** STUN (already proven on this node for browser TX) + keep the
   legacy path as the fallback + per-listener telemetry recording whether
   media ever flowed.
2. **If telemetry shows real failures:** hosted TURN first — an afternoon's
   work, no ports, nothing new in front of 443.
3. **Only if a third party is unacceptable:** coturn here with the demux,
   accepting the availability risk knowingly.

This keeps Phase 5's "delete the old pipeline" honest rather than silent: the
legacy path **stays**, demoted to a fallback, and that is now a decision
rather than an omission.

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

~~Side benefit: `rpt lstats` and the Connected Nodes list start showing the
real callsigns of who is listening.~~ **Wrong:** app_rpt hides `0`-prefixed
links from `rpt lstats`, `RPT_ALINKS` and the stats link list (see "Resolved:
session links are invisible" above). HenWen has to show them itself (issue #5).

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

### Phase 3 as built (2026-11-10)

- **Gate:** `GET /api/audio/session` returns the per-user credential only
  for an account with a usable identity (callsign + secret + includes
  wired), never for a listen-only account (D1-a: any credential can key;
  enforcing it inside the call is Phase 5) and never during a node lockout
  for a non-owner. Everyone else gets exactly the 1.x Listen and Arm TX.
- **Playback** goes through the existing `#node-audio` element via
  `srcObject`, so Volume, the speaker picker and Mute are shared with the
  1.x path. Chrome will not play remote WebRTC audio through WebAudio
  alone; the element is the reliable sink.
- **Mic:** requested at connect (D2). A refused mic becomes a silent
  synthetic track so RTP keeps flowing for `rtp_timeout`; PTT stays off.
  Not gated between presses: app_rpt zeroes a phone link's inbound audio
  while unkeyed (`app_rpt.c`, "if not receiving, zero-out audio"), verified
  in the 3.10.8 source during this phase.
- **Fallback (D3):** registration/INVITE failure, no ICE candidate,
  `_SIP_CONNECT_MS` (12s) without confirm, or zero inbound RTP
  `_SIP_MEDIA_MS` (6s) after confirm → one journal line
  (`sip-fallback`/`sip-nomedia`) and the 1.x stream. An unexpected `ended`
  retries once after 3s; two failures in 3 minutes pin the page load to
  the stream. `sip-connected`/`sip-media`/`sip-stats` are the positive
  telemetry.
- **Arm TX** is hidden only for accounts the one-call path serves; its code
  path is untouched for everyone else and is what the fallback leaves in
  place. `_sipTeardown()` is a no-op unless a one-call attempt existed,
  because `_stopListen()` runs on every internal 1.x reconnect and an armed
  Arm-TX call must survive those.
- **Logout ends the call** — the credential in use is the account's.

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
| **0** ✅ | Repo, history, remotes; `/opt/HenWen2` + unit + DB + port; docs; floor rewrite | HenWen2 runs alongside HenWen 1, 1.x untouched |
| **1** ✅ | Spikes — run against 643930 itself, which is radioless, so no lab Asterisk was needed | Both answered by measurement: the call downlink is self-free (0.0000 rms), and `outstreamcmd` carries the on-air mix including telemetry |
| **2** ✅ | `users.callsign`, per-user SIP provisioning, per-user dialplan/context, revocation | A second account registers and hears node audio on its own credential. **Built:** PR #1 (schema, renderers, writer) merged; PR #2 (assignment route + Manager field, provisioning trigger + reload, revocation incl. hangup of live calls, `sip-users/apply.sh`, `/api/tx/config` per-user identity, connector exclusion) open and deployed. **Verified live 2026-11-10** (N8GMZ registered and heard node audio on its own credential). |
| **3** ✅ | Frontend: one call = audio; connect / MUTE / PTT | Two accounts connected at once, neither hears itself, PTT keys. **Built (PR #3):** `/api/audio/session` + `_doStartListenSIP()`; mic up front, PTT bar on confirm, playback-side Mute, no keyed RX mute, 1.x stream as the instrumented fallback. **Verified live 2026-11-10** (three accounts at once, no self-echo, PTT from each, zero fallbacks). |
| **4** | Recording + stream relay onto `outstreamcmd` | A mid-QSO forced ID provably appears in a recording |
| **5** | Delete the old pipeline and its tests; wire `listen_only`; review the `_sip_session_link()` exclusions (app_rpt already hides `0`-prefixed links from lstats/ALINKS); show session users on the board as their own rows (issue #5, may land earlier) | Full suite green on a smaller codebase |
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
