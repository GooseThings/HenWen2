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
| D2 | Does an explicit "arm" step stay before keying RF? | Open |
| D3 | HTTPS mandatory (no choice — WebRTC); do we ship coturn? | Open |
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
